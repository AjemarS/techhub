"""
Domain service for order business logic.
Encapsulates order creation, status transitions, and inventory interactions.
"""
import random
import time
import logging
from decimal import Decimal

import stripe
from django.db import IntegrityError, transaction
from django.utils import timezone

from orders import metrics
from orders import promo as promo_svc
from orders.models import Order, OrderItem, OrderStatusHistory
from orders.eventbus import publish_event
from orders.inventory_client import reserve_stock, deduct_stock_batch, release_stock, return_stock
from orders.event_builder import build_order_event

logger = logging.getLogger(__name__)


def generate_order_number(attempt=0):
    """Generate a unique order number with collision-safe suffix."""
    ts = int(time.time() * 1000) % 100000
    rnd = random.randint(100, 999)
    if attempt == 0:
        return f"ORD-{ts}{rnd}"
    return f"ORD-{ts}{rnd}-{attempt}"


def calculate_total(items_data):
    """Calculate order total from validated items data."""
    return sum(Decimal(str(i["price"])) * i["quantity"] for i in items_data)


def create_order(serializer_data, items_data, channel, user_id):
    """
    Atomically create an order with its items. Retries on order number collision.
    Returns the created Order instance.
    """
    for attempt in range(3):
        try:
            order_number = generate_order_number(attempt)
            with transaction.atomic():
                total = calculate_total(items_data)
                promo, discount = None, Decimal("0.00")
                if serializer_data.get("promo_code"):
                    promo, discount = promo_svc.evaluate(
                        serializer_data["promo_code"], total,
                        customer_email=serializer_data.get("customer_email", ""), created_by=user_id, lock=True,
                    )
                    promo_svc.record_use(promo)
                order = Order.objects.create(
                    order_number=order_number,
                    channel=channel,
                    status=Order.UNPAID,
                    warehouse_id=serializer_data.get("warehouse_id"),
                    delivery_method=serializer_data.get("delivery_method", Order.PICKUP),
                    shipping_city=serializer_data.get("shipping_city", ""),
                    shipping_address=serializer_data.get("shipping_address", ""),
                    shipping_cost=serializer_data.get("shipping_cost", Decimal("0.00")),
                    customer_name=serializer_data.get("customer_name", ""),
                    customer_phone=serializer_data.get("customer_phone", ""),
                    customer_email=serializer_data.get("customer_email", ""),
                    total_amount=total - discount,
                    promo_code=promo,
                    discount_amount=discount,
                    notes=serializer_data.get("notes", ""),
                    created_by=user_id,
                )
                order_items = [
                    OrderItem(
                        order=order,
                        product_id=item["product_id"],
                        product_name=item.get("product_name", ""),
                        quantity=item["quantity"],
                        price=item["price"],
                        cost_price=item.get("cost_price", Decimal("0.00")),
                        warranty_months=item.get("warranty_months"),
                    )
                    for item in items_data
                ]
                OrderItem.objects.bulk_create(order_items)

                transaction.on_commit(
                    lambda o=order: publish_event("order.created", build_order_event(o))
                )
                transaction.on_commit(lambda c=channel: metrics.ORDERS_CREATED.labels(c).inc())

            logger.info(
                "Order created | number=%s channel=%s items=%d",
                order.order_number, channel, len(order_items),
            )
            return order

        except IntegrityError:
            if attempt == 2:
                logger.error("Order number collision exhausted | channel=%s", channel)
                raise
            continue


def record_status_change(order, from_status, request=None):
    """Append the order's current status to its history (call after the new status is saved)."""
    changed_by = ""
    if request is not None:
        changed_by = request.META.get("HTTP_X_GATEWAY_USER_ID", "") or getattr(request.user, "username", "")
    OrderStatusHistory.objects.create(
        order=order, from_status=from_status, to_status=order.status, changed_by=changed_by,
    )


def update_order_status(order, new_status, request=None, release_reserved=True):
    """
    Update order status with inventory side effects.
    release_reserved=False: the caller already released what this order had reserved (failed saga).
    Returns (order, error_response) where error_response is None on success.
    """
    if not order.can_transition_to(new_status):
        return order, {
            "error": f"Cannot transition from {order.status} to {new_status}",
            "allowed": Order.STATUS_TRANSITIONS.get(order.status, []),
        }

    old_status = order.status

    # Money goes back before the order is cancelled: a failed refund leaves it as it was
    if new_status == Order.CANCELLED and order.payment_status == Order.PAYMENT_PAID and order.stripe_payment_intent_id:
        try:
            # No amount: Stripe refunds whatever a return has not refunded already
            stripe.Refund.create(payment_intent=order.stripe_payment_intent_id, idempotency_key=f"cancel-{order.id}")
        except stripe.error.StripeError as e:
            logger.error("Stripe refund failed | order=%s error=%s", order.order_number, e)
            return order, {"error": "Refund failed", "detail": str(e)}
        order.payment_status = Order.PAYMENT_REFUNDED

    with transaction.atomic():
        order.status = new_status
        if new_status == Order.DELIVERED:
            order.delivered_at = timezone.now()
        order.save()
        transaction.on_commit(lambda: publish_event("order.status_changed", build_order_event(order)))
        if new_status == Order.CANCELLED:
            promo_svc.release_use(order)
            transaction.on_commit(lambda: metrics.ORDERS_CANCELLED.labels(order.channel).inc())
            transaction.on_commit(lambda: publish_event("order.cancelled", build_order_event(order)))

    # Release stock on cancellation; once shipped the reserve is already written off and the parcel comes back
    if new_status == Order.CANCELLED and order.warehouse_id and release_reserved:
        if old_status in (Order.UNPAID, Order.PAID):
            release_order_stock(order, request)
        else:
            return_order_stock(order, request)

    # Deduct reserved stock on shipping — all items or none, revert status on failure
    if new_status == Order.DELIVERING and order.warehouse_id:
        if not deduct_order_stock(order, from_reserved=True, request=request):
            order.status = old_status
            order.save(update_fields=["status"])
            logger.error(
                "Status reverted due to deduct failure | order=%s %s->%s",
                order.order_number, old_status, new_status,
            )
            return order, {"error": "Stock deduct failed, status reverted", "detail": "Inventory service error"}

    # Recorded only once the change has stuck: a reverted shipment leaves no trace in the history
    record_status_change(order, old_status, request)
    logger.info("Order status changed | number=%s %s->%s", order.order_number, old_status, new_status)
    return order, None


def release_order_stock(order, request=None):
    """Release reserved stock for a cancelled order."""
    for item in order.items.all():
        result, error = release_stock(
            product_id=item.product_id,
            warehouse_id=order.warehouse_id,
            quantity=item.quantity,
            reference_type="order",
            reference_id=str(order.id),
            idempotency_key=f"release-{order.id}-{item.product_id}",
            request=request,
        )
        if error:
            logger.error(
                "Stock release failed | order=%s product=%s error=%s",
                order.order_number, item.product_id, error,
            )


def return_order_stock(order, request=None):
    """Put a cancelled shipped order back on the shelf (its reserve was written off on shipping)."""
    for item in order.items.all():
        _, error = return_stock(
            product_id=item.product_id,
            warehouse_id=order.warehouse_id,
            quantity=item.quantity,
            reference_type="order",
            reference_id=str(order.id),
            idempotency_key=f"cancel-return-{order.id}-{item.product_id}",
            request=request,
        )
        if error:
            logger.error(
                "Stock return failed | order=%s product=%s error=%s",
                order.order_number, item.product_id, error,
            )


def deduct_order_stock(order, from_reserved, request=None):
    """
    Write off all order items in one inventory transaction, so a failure leaves nothing to compensate.
    from_reserved: the units were reserved for this order (shipping) rather than sold directly (POS).
    Returns True when every item was deducted.
    """
    result, error = deduct_stock_batch(
        warehouse_id=order.warehouse_id,
        items=[{"product_id": item.product_id, "quantity": item.quantity} for item in order.items.all()],
        from_reserved=from_reserved,
        reference_type="order",
        reference_id=str(order.id),
        idempotency_key=f"deduct-{order.id}",
        request=request,
    )
    if error:
        logger.error("Stock deduct failed | order=%s error=%s", order.order_number, error)
        return False
    # A 409 replay carries no costs; the first successful deduct already stored them
    for pid, cost in ((result or {}).get("unit_costs") or {}).items():
        order.items.filter(product_id=int(pid)).update(cost_price=Decimal(cost))
    return True


def reserve_order_stock(order, request=None):
    """
    Reserve stock for all items in an order.
    Returns (all_success, succeeded_items) where succeeded_items can be used for compensation.
    """
    all_success = True
    succeeded = []
    for item in order.items.all():
        result, error = reserve_stock(
            product_id=item.product_id,
            warehouse_id=order.warehouse_id,
            quantity=item.quantity,
            reference_type="order",
            reference_id=str(order.id),
            idempotency_key=f"reserve-{order.id}-{item.product_id}",
            request=request,
        )
        if error:
            logger.error(
                "Stock reserve failed | order=%s product=%s error=%s",
                order.order_number, item.product_id, error,
            )
            all_success = False
        else:
            succeeded.append((item.product_id, item.quantity))
    return all_success, succeeded
