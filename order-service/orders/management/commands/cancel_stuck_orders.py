import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders import order_service as svc
from orders.inventory_client import release_stock
from orders.models import Order

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Cancel and refund paid orders whose stock cannot be reserved (saga compensation)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout-minutes",
            type=int,
            default=30,
            help="Orders paid longer than this many minutes are checked",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list the orders that would be checked; inventory is not called",
        )

    def handle(self, *args, **options):
        timeout_minutes = options["timeout_minutes"]
        cutoff = timezone.now() - timezone.timedelta(minutes=timeout_minutes)
        # Without a warehouse nothing was ever reserved, so there is nothing to compensate
        candidates = Order.objects.filter(
            status=Order.PAID, paid_at__lt=cutoff, warehouse_id__isnull=False,
        ).prefetch_related("items")

        self.stdout.write(f"Checking {candidates.count()} orders paid > {timeout_minutes} min ago")
        for order in candidates:
            if options["dry_run"]:
                self.stdout.write(f"  Would check {order.order_number} ({order.id})")
            else:
                self._check(order)

    def _check(self, order):
        # Same idempotency keys as checkout: a reserve that exists replays (409), a missing one is made now
        ok, reserved = svc.reserve_order_stock(order)
        if ok:
            self.stdout.write(f"  {order.order_number}: reserve held")
            return

        for pid, qty in reserved:
            release_stock(
                product_id=pid, warehouse_id=order.warehouse_id, quantity=qty,
                reference_type="order", reference_id=str(order.id),
                idempotency_key=f"release-{order.id}-{pid}",
            )
        # Refunds through Stripe (once per order) and publishes order.cancelled
        _, error = svc.update_order_status(order, Order.CANCELLED, release_reserved=False)
        if error:
            logger.error("Stuck order not cancelled | order=%s error=%s", order.order_number, error)
            self.stdout.write(self.style.ERROR(f"  {order.order_number}: {error['error']}"))
            return
        logger.warning("Stuck order cancelled and refunded | order=%s", order.order_number)
        self.stdout.write(self.style.SUCCESS(f"  {order.order_number}: cancelled, refunded"))
