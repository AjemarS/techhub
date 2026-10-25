import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from products import catalog_cache
from products.models import Product

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Set Product.stock to the inventory total wherever a lost event left them apart"

    def handle(self, *args, **options):
        try:
            resp = requests.get(f"{settings.INVENTORY_SERVICE_URL}/api/stock/", timeout=30)
            resp.raise_for_status()
            rows = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise CommandError(f"Inventory unavailable: {e}") from e

        totals: dict[int, int] = {}
        for row in rows:
            totals[row["product_id"]] = totals.get(row["product_id"], 0) + row["quantity"]

        # Products with no stock line in inventory are left alone: nothing to compare them with.
        # shortcut: an event in flight during the run can leave a one-off drift,
        # fixed by the next run
        fixed = 0
        for product in Product.objects.filter(pk__in=totals):
            actual = totals[product.pk]
            if product.stock != actual:
                logger.warning(
                    "Stock drift | product=%s catalog=%s inventory=%s",
                    product.pk, product.stock, actual,
                )
                Product.objects.filter(pk=product.pk).update(stock=actual, in_stock=actual > 0)
                fixed += 1

        if fixed:
            catalog_cache.bump()
        logger.info("Stock reconciled | checked=%d fixed=%d", len(totals), fixed)
        self.stdout.write(self.style.SUCCESS(f"stock_reconcile_fixed {fixed}"))
