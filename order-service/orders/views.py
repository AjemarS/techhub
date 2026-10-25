"""
API views for order management.
Thin layer that validates input, delegates to order_service, and formats HTTP responses.
"""
import logging

import stripe
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone as tz
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from orders.models import Order, OrderItem
from orders import metrics
from orders import order_service as svc
from orders.inventory_client import release_stock
from orders.serializers import (
    OrderCreateSerializer,
    OrderDetailForAdminSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderStatusSerializer,
    POSOrderSerializer,
)
from shared_auth.permissions import IsAdminOrCashier

stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)


PAY_TOKEN_SALT = "orders.pay"
PAY_TOKEN_MAX_AGE = 7 * 24 * 3600


def _may_pay(order, request):
    user = request.user
    if user.is_authenticated and (user.is_staff or order.created_by == user.username):
        return True
    try:
        return signing.loads(request.data.get("token", ""), salt=PAY_TOKEN_SALT, max_age=PAY_TOKEN_MAX_AGE) == order.id
    except signing.BadSignature:
        return False


def _is_order_owner(order, request):
    user = request.user
    # created_by holds the buyer's username ("gw_<id>" behind the gateway), as create_order stores it
    return user.is_staff or order.created_by == user.username


def _handle_webhook_expired(order, session):
    if order.payment_status == Order.PAYMENT_UNPAID:
        logger.info("Stripe session expired | order=%s", order.order_number)
    return Response({"status": "ok"})


def _handle_webhook_payment_failed(order, session):
    error_msg = session.get("last_payment_error", {}).get("message", "Unknown error")
    metrics.PAYMENTS.labels("failed").inc()
    logger.error("Payment failed | order=%s error=%s", order.order_number, error_msg)
    return Response({"status": "ok"})


def _handle_webhook_completed(order, session, request):
    if order.payment_status == Order.PAID:
        return Response({"status": "already_paid"})

    with transaction.atomic():
        order.payment_status = Order.PAYMENT_PAID
        order.stripe_payment_intent_id = session.get("payment_intent", "")
        order.paid_at = tz.now()
        old_status, order.status = order.status, Order.PAID
        order.save()
        svc.record_status_change(order, old_status)

    if order.warehouse_id:
        success, succeeded = svc.reserve_order_stock(order)
        if not success:
            for pid, qty in succeeded:
                release_stock(
                    product_id=pid,
                    warehouse_id=order.warehouse_id,
                    quantity=qty,
                    reference_type="order",
                    reference_id=str(order.id),
                    idempotency_key=f"release-{order.id}-{pid}",
                    request=request,
                )
            # Actually refund the Stripe payment
            if order.stripe_payment_intent_id:
                try:
                    stripe.Refund.create(payment_intent=order.stripe_payment_intent_id)
                    logger.info("Stripe refunded | order=%s intent=%s", order.order_number, order.stripe_payment_intent_id)
                except stripe.error.StripeError as e:
                    logger.error("Stripe refund failed | order=%s error=%s", order.order_number, e)
            order.payment_status = Order.PAYMENT_REFUNDED
            order.save(update_fields=["payment_status"])
            svc.update_order_status(order, Order.CANCELLED, request, release_reserved=False)
            logger.error("Order cancelled after payment — reserve failure | number=%s", order.order_number)
            return Response({"status": "reserve_failed"})

    metrics.PAYMENTS.labels("paid").inc()
    logger.info("Payment confirmed | order=%s", order.order_number)
    return Response({"status": "ok"})


WEBHOOK_HANDLERS = {
    "checkout.session.expired": _handle_webhook_expired,
    "payment_intent.payment_failed": _handle_webhook_payment_failed,
    "checkout.session.completed": _handle_webhook_completed,
}


class OrderViewSet(viewsets.ModelViewSet):
    """ViewSet for order CRUD and business actions."""

    queryset = Order.objects.prefetch_related("items").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "channel", "customer_email"]
    search_fields = ["order_number", "customer_name", "customer_phone", "customer_email"]
    ordering_fields = ["created_at", "total_amount", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return OrderListSerializer
        if self.action in ("create", "pos"):
            return OrderCreateSerializer
        if self.action in ("pay", "stripe_webhook"):
            return None
        if self.action == "retrieve" and getattr(self.request.user, "is_staff", False):
            return OrderDetailForAdminSerializer
        return OrderDetailSerializer

    def get_permissions(self):
        if self.action in ("stripe_webhook", "pay"):
            # pay checks the buyer itself (_may_pay): guests prove it with the token from create
            return [AllowAny()]
        if self.action == "health":
            return [AllowAny()]
        # cancel checks the owner itself (_is_order_owner)
        if self.action in ("list", "my", "retrieve", "purchased", "cancel"):
            return [IsAuthenticated()]
        if self.action == "create":
            return [AllowAny()]
        if self.action == "pos":
            return [IsAdminOrCashier()]
        return [IsAdminUser()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if self.action == "retrieve" and not user.is_staff:
            return qs.filter(created_by=user.username)
        if self.action in ("my",) or (self.action == "list" and not user.is_staff):
            return qs.filter(created_by=user.username)
        return qs

    # ── Standard CRUD ────────────────────────────────────────────────

    def create(self, request, *args, **kwargs):
        """Create an online order with stock reserve and saga compensation."""
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_data = data.pop("items")

        # Stock was already checked by the serializer (_validate_stock), once per item
        warehouse_id = data.get("warehouse_id")
        user_id = request.user.username if request.user.is_authenticated else ""

        order = svc.create_order(
            serializer_data=data,
            items_data=items_data,
            channel=data.get("channel", Order.ONLINE),
            user_id=user_id,
        )

        # ── Reserve stock with saga compensation ──────────────────────
        if warehouse_id:
            all_success, succeeded = svc.reserve_order_stock(order, request)
            if not all_success:
                # Release all successfully reserved items (best effort)
                for pid, qty in succeeded:
                    release_stock(
                        product_id=pid,
                        warehouse_id=warehouse_id,
                        quantity=qty,
                        reference_type="order",
                        reference_id=str(order.id),
                        idempotency_key=f"compensate-reserve-{order.id}-{pid}",
                        request=request,
                    )

                # Record the reason, then cancel through the service so order.cancelled is published
                error_reason = "Stock reservation failed during order creation"
                order.notes = (order.notes + "\n" + error_reason).strip()
                order.save(update_fields=["notes"])
                svc.update_order_status(order, Order.CANCELLED, request, release_reserved=False)

                logger.error(
                    "Order cancelled — reserve failure | number=%s",
                    order.order_number,
                )

                return Response(
                    {
                        "error": "Stock reservation failed",
                        "reason": error_reason,
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            logger.info(
                "Stock reserved | order=%s items=%d",
                order.order_number, len(succeeded),
            )

        # A guest has no account to own the order: the signed token is what lets them pay for it
        return Response(
            {**OrderDetailSerializer(order).data, "pay_token": signing.dumps(order.id, salt=PAY_TOKEN_SALT)},
            status=status.HTTP_201_CREATED,
        )

    # ── Custom Actions ────────────────────────────────────────────────

    @action(detail=True, methods=["patch"])
    def status(self, request, pk=None):
        """Update order status with inventory side effects."""
        order = self.get_object()
        serializer = OrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        updated_order, error = svc.update_order_status(order, new_status, request)
        if error:
            is_transition_error = error.get("allowed") is not None
            code = status.HTTP_400_BAD_REQUEST if is_transition_error else status.HTTP_502_BAD_GATEWAY
            return Response(error, status=code)

        return Response(OrderDetailSerializer(updated_order).data)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """Create Stripe checkout session for payment: the buyer (account or pay_token) or staff."""
        order = self.get_object()
        if not _may_pay(order, request):
            # 404, not 403: do not confirm that someone else's order id exists
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if order.payment_status != Order.PAYMENT_UNPAID:
            return Response(
                {"error": "Order already paid", "payment_status": order.payment_status},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Lines stay at catalog price so the receipt lists them; the promo is a one-off coupon on top
            discounts = []
            if order.discount_amount > 0:
                coupon = stripe.Coupon.create(
                    amount_off=int(order.discount_amount * 100), currency="uah", duration="once",
                    name=order.promo_code.code if order.promo_code else "Discount",
                    idempotency_key=f"coupon-{order.id}",
                )
                discounts = [{"coupon": coupon.id}]
            session = stripe.checkout.Session.create(
                discounts=discounts,
                line_items=[
                    {
                        "price_data": {
                            "currency": "uah",
                            "product_data": {"name": item.product_name or f"Product #{item.product_id}"},
                            "unit_amount": int(item.price * 100),
                        },
                        "quantity": item.quantity,
                    }
                    for item in order.items.all()
                ],
                mode="payment",
                client_reference_id=str(order.id),
                customer_email=order.customer_email or None,
                metadata={"order_id": order.id},
                success_url=settings.PUBLIC_BASE_URL + "/checkout/success?order_id=" + str(order.id),
                cancel_url=settings.PUBLIC_BASE_URL + "/checkout?order_id=" + str(order.id),
                idempotency_key=f"pay-{order.id}",
            )
            order.stripe_session_id = session.id
            order.save(update_fields=["stripe_session_id"])
            return Response({"checkout_url": session.url, "session_id": session.id})

        except stripe.error.StripeError as e:
            logger.error("Stripe session failed | order=%s error=%s", order.order_number, e)
            return Response({"error": "Payment session creation failed"}, status=status.HTTP_502_BAD_GATEWAY)

    @action(detail=False, methods=["post"])
    def stripe_webhook(self, request):
        """Handle Stripe events with saga compensation."""
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
        except ValueError:
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.SignatureVerificationError:
            return Response({"error": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        session = event["data"]["object"]
        order_id = session.get("metadata", {}).get("order_id")
        if not order_id:
            return Response({"error": "Missing order_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order = Order.objects.prefetch_related("items").get(pk=order_id)
        except Order.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

        handler = WEBHOOK_HANDLERS.get(event["type"])
        if not handler:
            return Response({"status": "ok"})

        return handler(order, session, request) if handler is _handle_webhook_completed else handler(order, session)

    @action(detail=False, methods=["get"])
    def my(self, request):
        """Return current user's orders."""
        if not request.user.is_authenticated:
            return Response({"error": "Authentication required"}, status=401)
        orders = self.filter_queryset(self.get_queryset())  # get_queryset keeps only this user's orders
        page = self.paginate_queryset(orders)
        if page is not None:
            serializer = OrderListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = OrderListSerializer(orders, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def purchased(self, request):
        """Whether the current user received this product (?product_id=): product-service gates reviews on it."""
        try:
            product_id = int(request.query_params.get("product_id", ""))
        except ValueError:
            return Response({"error": "product_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)
        bought = OrderItem.objects.filter(
            product_id=product_id,
            order__created_by=request.user.username,
            order__status__in=(Order.DELIVERED, Order.COMPLETED),
        ).exists()
        return Response({"purchased": bought})

    @action(detail=False, methods=["post"])
    def pos(self, request):
        """Create POS (offline) sale with immediate stock deduct."""
        serializer = POSOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        items_data = data.pop("items")
        user_id = request.user.username if request.user.is_authenticated else ""

        order = svc.create_order(
            serializer_data=data,
            items_data=items_data,
            channel=Order.OFFLINE,
            user_id=user_id,
        )

        if not svc.deduct_order_stock(order, from_reserved=False, request=request):
            svc.update_order_status(order, Order.CANCELLED, request, release_reserved=False)
            logger.error("POS sale cancelled — deduct failure | number=%s", order.order_number)
            return Response(
                {"error": "Stock deduct failed", "status": Order.CANCELLED, "order": OrderDetailSerializer(order).data},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # The cashier takes payment at the till and the buyer leaves with the goods: nothing left to track
        order.status = Order.COMPLETED
        order.payment_status = Order.PAYMENT_PAID
        order.paid_at = order.delivered_at = tz.now()
        order.save(update_fields=["status", "payment_status", "paid_at", "delivered_at", "updated_at"])
        svc.record_status_change(order, Order.UNPAID, request)

        return Response(OrderDetailSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel own unpaid or paid order."""
        order = self.get_object()
        if not _is_order_owner(order, request):
            return Response({"error": "Not your order"}, status=status.HTTP_403_FORBIDDEN)
        if order.status not in (Order.UNPAID, Order.PAID):
            return Response(
                {"error": f"Cannot cancel order in status {order.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updated_order, error = svc.update_order_status(order, Order.CANCELLED, request)
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(updated_order).data)

    @action(detail=False, methods=["get"])
    def health(self, request):
        """Service health check."""
        return Response({"status": "healthy", "service": "order-service", "database": "connected"})
