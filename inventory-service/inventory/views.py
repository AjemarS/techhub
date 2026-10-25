import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Sum
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, views, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .eventbus import publish_event, _check_and_publish_low_stock
from .filters import StockFilter, StockMovementFilter
from .models import ActivityEvent, GoodsReceiptItem, GoodsReceiptNote, PurchaseOrder, Stock, StockMovement, Supplier, Warehouse
from .purchasing import receive_against_order, reorder_suggestions
from shared_auth.permissions import IsAdminOrWarehouseWorker
from .serializers import (
    ActivityEventSerializer,
    AdjustStockSerializer,
    DeductBatchSerializer,
    DeductStockSerializer,
    GoodsReceiptNoteCreateSerializer,
    GoodsReceiptNoteSerializer,
    ProductInfoInputSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderStatusSerializer,
    ReorderSettingsSerializer,
    ReserveStockSerializer,
    StockMovementSerializer,
    StockSerializer,
    SupplierSerializer,
    TransferStockSerializer,
    WarehouseSerializer,
)
logger = logging.getLogger(__name__)


def _author(request):
    """Who did it, as people read it in the admin: name, else email ("gw_<id>" means nothing to them)."""
    user = request.user
    if not user.is_authenticated:
        return ""
    return user.first_name or user.email or user.username


def _stock_action(request, serializer_cls, movement_type, check_and_update, post_action=None, inbound=False):
    serializer = serializer_cls(data=request.data)
    serializer.is_valid(raise_exception=True)

    product_id = serializer.validated_data["product_id"]
    warehouse_id = serializer.validated_data["warehouse_id"]
    quantity = serializer.validated_data["quantity"]
    reference_type = serializer.validated_data.get("reference_type", "order")
    reference_id = serializer.validated_data.get("reference_id", "")
    idempotency_key = serializer.validated_data.get("idempotency_key", "")

    if idempotency_key:
        existing = StockMovement.objects.filter(
            idempotency_key=idempotency_key,
            type=movement_type,
        ).exists()
        if existing:
            stock = Stock.objects.get(product_id=product_id, warehouse_id=warehouse_id)
            return Response(StockSerializer(stock).data, status=status.HTTP_409_CONFLICT)

    with transaction.atomic():
        stock = Stock.objects.select_for_update().get(
            product_id=product_id, warehouse_id=warehouse_id
        )
        error = check_and_update(stock, product_id, warehouse_id, quantity)
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)

        stock.refresh_from_db()

        StockMovement.objects.create(
            product_id=product_id,
            # Goods coming in (a customer return) land in the warehouse rather than leave it
            **({"to_warehouse_id": warehouse_id} if inbound else {"from_warehouse_id": warehouse_id}),
            quantity=quantity,
            type=movement_type,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            created_by=_author(request),
        )

    if post_action:
        post_action(product_id, warehouse_id, quantity, stock)

    return Response(StockSerializer(stock).data)


def _publish_deducted(product_id, warehouse_id, quantity, warehouse_name):
    current = Stock.objects.get(product_id=product_id, warehouse_id=warehouse_id)
    publish_event("inventory.stock.changed", {
        "event_id": str(uuid4()),
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "quantity": current.quantity,
        "change": -quantity,
    })
    _check_and_publish_low_stock(product_id, warehouse_id, current.quantity, warehouse_name=warehouse_name)


class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "address"]
    ordering_fields = ["name", "type", "created_at"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "stock"):
            return [IsAuthenticatedOrReadOnly()]
        return [IsAdminUser()]

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info("Warehouse created | id=%s name=%r", instance.pk, instance.name)

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info("Warehouse updated | id=%s name=%r", instance.pk, instance.name)

    def perform_destroy(self, instance):
        logger.info("Warehouse deleted | id=%s name=%r", instance.pk, instance.name)
        instance.delete()

    @action(detail=True, methods=["get"])
    def stock(self, request, pk=None):
        warehouse = self.get_object()
        stocks = Stock.objects.filter(warehouse=warehouse).select_related("warehouse")
        page = self.paginate_queryset(stocks)
        if page is not None:
            serializer = StockSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = StockSerializer(stocks, many=True)
        return Response(serializer.data)


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "contact_person", "email", "phone"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAdminOrWarehouseWorker()]
        return [IsAdminUser()]

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info("Supplier created | id=%s name=%r", instance.pk, instance.name)

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info("Supplier updated | id=%s name=%r", instance.pk, instance.name)

    def perform_destroy(self, instance):
        logger.info("Supplier deleted | id=%s name=%r", instance.pk, instance.name)
        instance.delete()


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Stock.objects.select_related("warehouse").all()
    serializer_class = StockSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = StockFilter
    ordering_fields = ["product_id", "quantity", "warehouse__name"]
    ordering = ["product_id"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticatedOrReadOnly()]
        if self.action in ("reserve", "deduct", "deduct_batch", "release", "return_to_stock"):
            return [IsAuthenticated()]
        if self.action in ("transfer", "adjust", "movements", "reorder", "reorder_suggestions"):
            return [IsAdminOrWarehouseWorker()]
        return [IsAdminUser()]

    @action(detail=True, methods=["patch"])
    def reorder(self, request, pk=None):
        """Set when (reorder_point) and how much (reorder_quantity) to reorder this stock line."""
        stock = self.get_object()
        data = ReorderSettingsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        Stock.objects.filter(pk=stock.pk).update(**data.validated_data)
        stock.refresh_from_db()
        return Response(StockSerializer(stock, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="reorder-suggestions")
    def reorder_suggestions(self, request):
        warehouse_id = request.query_params.get("warehouse_id")
        if warehouse_id and not warehouse_id.isdigit():
            raise ValidationError({"warehouse_id": "Expected an integer"})
        return Response(reorder_suggestions(int(warehouse_id) if warehouse_id else None))

    @action(detail=False, methods=["post"])
    def reserve(self, request):
        def check_and_update(stock, product_id, warehouse_id, quantity):
            available = stock.quantity - stock.reserved
            if available < quantity:
                return {"error": "Insufficient available stock", "available": available, "requested": quantity}
            Stock.objects.filter(product_id=product_id, warehouse_id=warehouse_id).update(reserved=F("reserved") + quantity)
            logger.info("Stock reserved | product=%s warehouse=%s quantity=%d", product_id, warehouse_id, quantity)

        return _stock_action(request, ReserveStockSerializer, StockMovement.RESERVE, check_and_update)

    @action(detail=False, methods=["post"])
    def release(self, request):
        def check_and_update(stock, product_id, warehouse_id, quantity):
            if stock.reserved < quantity:
                return {"error": "Cannot release more than reserved", "reserved": stock.reserved, "requested": quantity}
            Stock.objects.filter(product_id=product_id, warehouse_id=warehouse_id).update(reserved=F("reserved") - quantity)
            logger.info("Stock released | product=%s warehouse=%s quantity=%d", product_id, warehouse_id, quantity)

        return _stock_action(request, ReserveStockSerializer, StockMovement.RELEASE, check_and_update)

    @action(detail=False, methods=["post"])
    def return_to_stock(self, request):
        """A returned unit goes back on the shelf (service call from order-service returns)."""
        def check_and_update(stock, product_id, warehouse_id, quantity):
            Stock.objects.filter(product_id=product_id, warehouse_id=warehouse_id).update(quantity=F("quantity") + quantity)
            logger.info("Stock returned | product=%s warehouse=%s quantity=%d", product_id, warehouse_id, quantity)

        def publish(product_id, warehouse_id, quantity, stock):
            transaction.on_commit(lambda: publish_event("inventory.stock.changed", {
                "event_id": str(uuid4()), "product_id": product_id, "warehouse_id": warehouse_id,
                "quantity": stock.quantity, "change": quantity,
            }))

        return _stock_action(request, ReserveStockSerializer, StockMovement.RETURN, check_and_update, publish, inbound=True)

    @action(detail=False, methods=["post"])
    def deduct(self, request):
        def check_and_update(stock, product_id, warehouse_id, quantity):
            available = stock.quantity - stock.reserved
            if available < quantity:
                return {"error": "Insufficient available stock", "available": available, "requested": quantity}
            Stock.objects.filter(product_id=product_id, warehouse_id=warehouse_id).update(
                quantity=F("quantity") - quantity,
                reserved=F("reserved") - min(stock.reserved, quantity),
            )
            logger.info("Stock deducted | product=%s warehouse=%s quantity=%d", product_id, warehouse_id, quantity)

        def post_action(product_id, warehouse_id, quantity, stock):
            transaction.on_commit(lambda: _publish_deducted(product_id, warehouse_id, quantity, stock.warehouse.name))

        return _stock_action(request, DeductStockSerializer, StockMovement.DEDUCT, check_and_update, post_action=post_action)

    @action(detail=False, methods=["post"])
    def deduct_batch(self, request):
        """Write off all items of one order in a single transaction: every item or none."""
        serializer = DeductBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        warehouse_id, key, from_reserved = data["warehouse_id"], data["idempotency_key"], data["from_reserved"]
        wanted = {}
        for item in data["items"]:
            wanted[item["product_id"]] = wanted.get(item["product_id"], 0) + item["quantity"]

        with transaction.atomic():
            # Lock rows in product order so concurrent batches cannot deadlock
            stocks = {s.product_id: s for s in Stock.objects.select_for_update().select_related("warehouse")
                      .filter(warehouse_id=warehouse_id, product_id__in=wanted).order_by("product_id")}
            item_keys = {pid: f"{key}-{pid}" for pid in wanted} if key else {}
            if item_keys and StockMovement.objects.filter(type=StockMovement.DEDUCT, idempotency_key__in=item_keys.values()).exists():
                return Response({"items": StockSerializer(stocks.values(), many=True).data}, status=status.HTTP_409_CONFLICT)

            for pid, qty in wanted.items():
                stock = stocks.get(pid)
                if stock is None:
                    return Response({"error": "No stock record", "product_id": pid}, status=status.HTTP_400_BAD_REQUEST)
                enough = (stock.reserved >= qty and stock.quantity >= qty) if from_reserved else stock.available >= qty
                if not enough:
                    return Response({
                        "error": "Insufficient reserved stock" if from_reserved else "Insufficient available stock",
                        "product_id": pid, "quantity": stock.quantity, "reserved": stock.reserved, "requested": qty,
                    }, status=status.HTTP_400_BAD_REQUEST)

            for pid, qty in wanted.items():
                Stock.objects.filter(pk=stocks[pid].pk).update(
                    quantity=F("quantity") - qty,
                    reserved=F("reserved") - (qty if from_reserved else 0),
                )
                StockMovement.objects.create(
                    product_id=pid, from_warehouse_id=warehouse_id, quantity=qty, type=StockMovement.DEDUCT,
                    reference_type=data["reference_type"], reference_id=data["reference_id"],
                    idempotency_key=item_keys.get(pid, ""),
                    created_by=_author(request),
                )
                transaction.on_commit(lambda pid=pid, qty=qty: _publish_deducted(pid, warehouse_id, qty, stocks[pid].warehouse.name))
            logger.info("Stock batch deducted | warehouse=%s items=%s from_reserved=%s", warehouse_id, wanted, from_reserved)

        for stock in stocks.values():
            stock.refresh_from_db()
        return Response({
            "items": StockSerializer(stocks.values(), many=True).data,
            # Cost of goods sold for the caller's order lines; deduct is service-only, so cost stays private
            "unit_costs": {str(pid): str(stocks[pid].average_cost) for pid in wanted},
        })

    @action(detail=False, methods=["post"])
    def transfer(self, request):
        serializer = TransferStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data["product_id"]
        from_warehouse_id = serializer.validated_data["from_warehouse_id"]
        to_warehouse_id = serializer.validated_data["to_warehouse_id"]
        quantity = serializer.validated_data["quantity"]
        reference_type = serializer.validated_data.get("reference_type", "transfer")
        reference_id = serializer.validated_data.get("reference_id", "")
        notes = serializer.validated_data.get("notes", "")

        if from_warehouse_id == to_warehouse_id:
            return Response(
                {"error": "Source and destination warehouses must be different"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            source = Stock.objects.select_for_update().get(
                product_id=product_id, warehouse_id=from_warehouse_id
            )
            dest, _ = Stock.objects.get_or_create(
                product_id=product_id,
                warehouse_id=to_warehouse_id,
                defaults={"quantity": 0},
            )
            dest = Stock.objects.select_for_update().get(pk=dest.pk)

            available = source.quantity - source.reserved
            if available < quantity:
                return Response(
                    {
                        "error": "Insufficient available stock at source",
                        "available": available,
                        "requested": quantity,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            Stock.objects.filter(
                product_id=product_id, warehouse_id=from_warehouse_id
            ).update(quantity=F("quantity") - quantity)
            # Units carry their cost along: the destination's average is weighted by what arrives
            dest_cost = (dest.quantity * dest.average_cost + quantity * source.average_cost) / (dest.quantity + quantity)
            Stock.objects.filter(
                product_id=product_id, warehouse_id=to_warehouse_id
            ).update(quantity=F("quantity") + quantity, average_cost=dest_cost)

            source.refresh_from_db()
            dest.refresh_from_db()

            StockMovement.objects.create(
                product_id=product_id,
                from_warehouse_id=from_warehouse_id,
                to_warehouse_id=to_warehouse_id,
                quantity=quantity,
                type=StockMovement.TRANSFER,
                reference_type=reference_type,
                reference_id=reference_id,
                notes=notes,
                created_by=_author(request),
            )

            _check_and_publish_low_stock(
                product_id, from_warehouse_id, source.quantity,
                warehouse_name=source.warehouse.name,
            )

            logger.info(
                "Stock transfer | product=%s from=%s to=%s quantity=%d",
                product_id, from_warehouse_id, to_warehouse_id, quantity,
            )

        return Response({
            "source": StockSerializer(source).data,
            "destination": StockSerializer(dest).data,
        })

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        serializer = AdjustStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data["product_id"]
        warehouse_id = serializer.validated_data["warehouse_id"]
        new_quantity = serializer.validated_data["new_quantity"]
        reason = serializer.validated_data.get("reason", "")

        with transaction.atomic():
            stock = Stock.objects.select_for_update().get(
                product_id=product_id, warehouse_id=warehouse_id
            )

            if new_quantity < stock.reserved:
                return Response(
                    {"error": "Quantity cannot be below reserved", "reserved": stock.reserved, "requested": new_quantity},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            delta = new_quantity - stock.quantity

            Stock.objects.filter(
                product_id=product_id, warehouse_id=warehouse_id
            ).update(quantity=F("quantity") + delta)
            stock.refresh_from_db()

            StockMovement.objects.create(
                product_id=product_id,
                to_warehouse_id=warehouse_id,
                quantity=abs(delta),
                type=StockMovement.ADJUSTMENT,
                reference_type="adjustment",
                notes=f"{reason} (delta: {delta:+d})" if reason else f"Delta: {delta:+d}",
                created_by=_author(request),
            )

            logger.info(
                "Stock adjusted | product=%s warehouse=%s delta=%+d new=%d",
                product_id, warehouse_id, delta, new_quantity,
            )

            if delta:
                transaction.on_commit(lambda: publish_event("inventory.stock.changed", {
                    "event_id": str(uuid4()), "product_id": product_id, "warehouse_id": warehouse_id,
                    "quantity": stock.quantity, "change": delta,
                }))
            transaction.on_commit(
                lambda: _check_and_publish_low_stock(
                    product_id, warehouse_id, stock.quantity,
                    warehouse_name=stock.warehouse.name,
                )
            )

        return Response(StockSerializer(stock).data)

    @action(detail=False, methods=["get"])
    def valuation(self, request):
        """On-hand stock valued at weighted-average cost (admin only, falls to IsAdminUser)."""
        on_hand = Stock.objects.filter(quantity__gt=0)
        value = ExpressionWrapper(F("quantity") * F("average_cost"), output_field=DecimalField())
        by_product = on_hand.values("product_id").annotate(
            total_quantity=Sum("quantity"), total_value=Sum(value),
        ).order_by("-total_value")
        rows = [
            {
                "product_id": r["product_id"],
                "total_quantity": r["total_quantity"],
                "total_value": float(r["total_value"] or 0),
            }
            for r in by_product
        ]
        return Response({
            "total_value": round(sum(r["total_value"] for r in rows), 2),
            "item_count": len(rows),
            # Stock that arrived without a GRN has no cost yet, so it adds nothing to total_value
            "uncosted_quantity": on_hand.filter(average_cost=0).aggregate(n=Sum("quantity"))["n"] or 0,
            "by_product": rows,
        })

    @action(detail=False, methods=["get"])
    def analytics(self, request):
        """
        Per-product stock health over the last ?days= (default 90): sell-through, days of cover,
        turnover and dead stock. Admin only (falls to IsAdminUser).
        """
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            raise ValidationError({"days": "Expected an integer"})
        if not 7 <= days <= 730:
            raise ValidationError({"days": "Expected 7..730"})
        since = timezone.now() - timedelta(days=days)

        value = ExpressionWrapper(F("quantity") * F("average_cost"), output_field=DecimalField())
        stock = {
            r["product_id"]: r
            for r in Stock.objects.values("product_id").annotate(
                on_hand=Sum("quantity"), reserved=Sum("reserved"), value=Sum(value),
            )
        }
        sold_types = [StockMovement.DEDUCT, StockMovement.SALE]
        out = StockMovement.objects.filter(type__in=sold_types)
        sold = dict(
            out.filter(created_at__gte=since).values("product_id")
            .annotate(n=Sum("quantity")).values_list("product_id", "n")
        )
        written_off = dict(
            StockMovement.objects.filter(type=StockMovement.WRITE_OFF, created_at__gte=since)
            .values("product_id").annotate(n=Sum("quantity")).values_list("product_id", "n")
        )
        last_sale = dict(out.values("product_id").annotate(at=Max("created_at")).values_list("product_id", "at"))

        rows = []
        for pid in set(stock) | set(sold):
            s = stock.get(pid, {})
            on_hand = s.get("on_hand") or 0
            units = sold.get(pid, 0)
            per_day = units / days
            stock_value = float(s.get("value") or 0)
            rows.append({
                "product_id": pid,
                "on_hand": on_hand,
                "reserved": s.get("reserved") or 0,
                "stock_value": round(stock_value, 2),
                "units_sold": units,
                "units_written_off": written_off.get(pid, 0),
                "avg_daily_sales": round(per_day, 3),
                # How long the current stock lasts at the window's sales pace; null = no sales to judge by
                "days_of_cover": round(on_hand / per_day, 1) if per_day else None,
                # shortcut: divides by current stock, not the average over the window; upgrade with daily snapshots
                "turnover": round(units / on_hand, 2) if on_hand else None,
                "last_sale_at": last_sale[pid].isoformat() if pid in last_sale else None,
                "dead": on_hand > 0 and units == 0,
            })
        rows.sort(key=lambda r: r["stock_value"], reverse=True)

        dead = [r for r in rows if r["dead"]]
        total_value = sum(r["stock_value"] for r in rows)
        total_on_hand = sum(r["on_hand"] for r in rows)
        total_sold = sum(r["units_sold"] for r in rows)
        return Response({
            "days": days,
            "summary": {
                "stock_value": round(total_value, 2),
                "units_on_hand": total_on_hand,
                "units_sold": total_sold,
                "turnover": round(total_sold / total_on_hand, 2) if total_on_hand else None,
                "dead_products": len(dead),
                "dead_stock_value": round(sum(r["stock_value"] for r in dead), 2),
                "dead_stock_share": round(sum(r["stock_value"] for r in dead) / total_value * 100, 2) if total_value else 0,
                # Will sell out within two weeks at the current pace
                "low_cover_products": sum(1 for r in rows if r["days_of_cover"] is not None and r["days_of_cover"] < 14),
            },
            "products": rows,
        })

    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        stock = self.get_object()
        movements = StockMovement.objects.filter(
            product_id=stock.product_id,
        ).select_related("from_warehouse", "to_warehouse")[:100]
        serializer = StockMovementSerializer(movements, many=True)
        return Response(serializer.data)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = StockMovement.objects.select_related(
        "from_warehouse", "to_warehouse"
    ).all()
    serializer_class = StockMovementSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = StockMovementFilter
    ordering_fields = ["created_at", "product_id"]
    ordering = ["-created_at"]

    def get_permissions(self):
        return [IsAdminOrWarehouseWorker()]


class GoodsReceiptNoteViewSet(viewsets.ModelViewSet):
    queryset = GoodsReceiptNote.objects.select_related(
        "supplier", "warehouse"
    ).prefetch_related("items").all()
    http_method_names = ["get", "post", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["supplier", "warehouse"]
    search_fields = ["reference_number", "notes"]
    ordering_fields = ["created_at", "receipt_date"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return GoodsReceiptNoteCreateSerializer
        return GoodsReceiptNoteSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "create", "product_info"):
            return [IsAdminOrWarehouseWorker()]
        return [IsAdminUser()]

    @action(detail=False, methods=["get"], url_path="supplier-stats")
    def supplier_stats(self, request):
        """Per-supplier delivery totals over all GRNs (admin only, falls to IsAdminUser)."""
        line_value = ExpressionWrapper(F("quantity") * F("cost_price"), output_field=DecimalField())
        rows = (
            GoodsReceiptItem.objects.values(
                supplier_id=F("goods_receipt__supplier_id"),
                supplier_name=F("goods_receipt__supplier__name"),
            )
            .annotate(
                total_items=Sum("quantity"),
                total_value=Sum(line_value),
                delivery_count=Count("goods_receipt", distinct=True),
                last_delivery=Max("goods_receipt__receipt_date"),
            )
            .order_by("-total_value")
        )
        return Response([
            {**r, "total_value": float(r["total_value"] or 0), "last_delivery": r["last_delivery"].isoformat()}
            for r in rows
        ])

    @action(detail=False, methods=["get"])
    def product_info(self, request):
        """Return last cost_price and current stock for a product+warehouse combo."""
        serializer = ProductInfoInputSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data["product_id"]
        warehouse_id = serializer.validated_data["warehouse_id"]

        # Get last cost price from most recent GRN item for this product
        last_item = GoodsReceiptItem.objects.filter(
            product_id=product_id,
            goods_receipt__warehouse_id=warehouse_id,
        ).select_related("goods_receipt").order_by("-goods_receipt__created_at").first()

        last_cost_price = str(last_item.cost_price) if last_item else None

        # Get current stock level
        try:
            stock = Stock.objects.get(product_id=product_id, warehouse_id=warehouse_id)
            current_stock = stock.quantity
        except Stock.DoesNotExist:
            current_stock = 0

        return Response({
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            "last_cost_price": last_cost_price,
            "current_stock": current_stock,
        })

    @transaction.atomic
    def perform_create(self, serializer):
        # One transaction: a delivery that does not match its purchase order leaves no stock behind
        instance = serializer.save(created_by=_author(self.request))
        if not instance.reference_number:
            # Unique by construction; the browser used to number receipts and repeated itself
            instance.reference_number = f"GRN-{instance.receipt_date:%Y%m%d}-{instance.pk:04d}"
            instance.save(update_fields=["reference_number"])

        for item in instance.items.all():
            stock, _ = Stock.objects.select_for_update().get_or_create(
                product_id=item.product_id,
                warehouse=instance.warehouse,
                defaults={"quantity": 0},
            )
            Stock.objects.filter(
                product_id=item.product_id, warehouse=instance.warehouse
            ).update(quantity=F("quantity") + item.quantity)
            stock.refresh_from_db()

            # Weighted-average cost recalculation
            old_qty = stock.quantity - item.quantity
            old_cost = stock.average_cost
            if old_qty > 0:
                new_avg = (old_qty * old_cost + item.quantity * item.cost_price) / (old_qty + item.quantity)
            else:
                new_avg = item.cost_price
            Stock.objects.filter(pk=stock.pk).update(average_cost=new_avg)

            StockMovement.objects.create(
                product_id=item.product_id,
                to_warehouse=instance.warehouse,
                quantity=item.quantity,
                type=StockMovement.RECEIPT,
                reference_type="goods_receipt",
                reference_id=str(instance.pk),
                notes=f"GRN #{instance.pk}: {item.quantity} units @ {item.cost_price}",
                created_by=instance.created_by,
            )

            logger.info(
                "Stock receipt | product=%s warehouse=%s quantity=%d cost=%s",
                item.product_id,
                instance.warehouse.name,
                item.quantity,
                item.cost_price,
            )

        for item in instance.items.all():
            transaction.on_commit(
                lambda item=item: publish_event(
                    "inventory.stock.changed",
                    {
                        "event_id": str(uuid4()),
                        "product_id": item.product_id,
                        "warehouse_id": instance.warehouse_id,
                        "quantity": Stock.objects.get(
                            product_id=item.product_id, warehouse=instance.warehouse
                        ).quantity,
                        "change": item.quantity,
                    },
                )
            )
            transaction.on_commit(
                lambda item=item: publish_event(
                    "inventory.goods_received",
                    {
                        "event_id": str(uuid4()),
                        "product_id": item.product_id,
                        "warehouse_id": instance.warehouse_id,
                        "quantity": item.quantity,
                        "cost_price": str(item.cost_price),
                        "grn_id": instance.pk,
                    },
                )
            )
        if instance.purchase_order_id:
            po = receive_against_order(instance, instance.purchase_order)
            logger.info("GRN %s received against PO %s -> %s", instance.pk, po.pk, po.status)
        logger.info("GRN created | id=%s supplier=%s", instance.pk, instance.supplier.name)

class ActivityEventViewSet(viewsets.ModelViewSet):
    queryset = ActivityEvent.objects.all()[:50]
    serializer_class = ActivityEventSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = None
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        user = self.request.user
        user_name = (
            getattr(user, "name", "") or user.get_full_name() or getattr(user, "email", "")
        )
        user_email = getattr(user, "email", "")
        serializer.save(user_name=user_name, user_email=user_email)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Health check with database connectivity probe."""
    try:
        connection.ensure_connection()
        return Response({
            "status": "healthy",
            "service": "inventory-service",
            "checks": {"database": "connected"},
        })
    except Exception:
        logger.exception("Health check failed")
        return Response(
            {
                "status": "unhealthy",
                "service": "inventory-service",
                "checks": {"database": "disconnected"},
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """Purchase orders: drafted by staff, sent or cancelled by an admin, received through goods receipts."""

    queryset = PurchaseOrder.objects.select_related("supplier", "warehouse").prefetch_related("items")
    serializer_class = PurchaseOrderSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "supplier", "warehouse"]
    ordering_fields = ["created_at", "expected_date"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action == "set_status":
            return [IsAdminUser()]
        return [IsAdminOrWarehouseWorker()]

    def perform_create(self, serializer):
        po = serializer.save(created_by=_author(self.request))
        logger.info("PO created | id=%s supplier=%s items=%d", po.pk, po.supplier_id, po.items.count())

    def perform_update(self, serializer):
        if serializer.instance.status != PurchaseOrder.DRAFT:
            raise ValidationError({"status": "Only a draft can be edited."})
        serializer.save()

    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, pk=None):
        po = self.get_object()
        data = PurchaseOrderStatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        new = data.validated_data["status"]
        allowed = {
            PurchaseOrder.SENT: (PurchaseOrder.DRAFT,),
            # Cancelling a partly delivered order closes what is still missing
            PurchaseOrder.CANCELLED: (PurchaseOrder.DRAFT, PurchaseOrder.SENT, PurchaseOrder.PARTIAL),
        }[new]
        if po.status not in allowed:
            return Response({"error": f"Cannot move order from {po.status} to {new}"}, status=status.HTTP_400_BAD_REQUEST)
        po.status = new
        if new == PurchaseOrder.SENT:
            po.sent_at = timezone.now()
        po.save(update_fields=["status", "sent_at", "updated_at"])
        logger.info("PO %s -> %s", po.pk, new)
        return Response(PurchaseOrderSerializer(po).data)
