import logging
import re

from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError

from .models import Order, OrderItem, ReturnRequest
from .inventory_client import check_availability, pick_fulfilment_warehouse
from .product_client import CatalogUnavailable, get_catalog_items

logger = logging.getLogger(__name__)


class InventoryServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Stock cannot be checked right now, try again later."
    default_code = "inventory_unavailable"


def _assign_fulfilment_warehouse(data):
    """An online order always leaves from a warehouse, or nothing would be reserved and written off."""
    if data.get("warehouse_id"):
        return
    if data.get("delivery_method", Order.PICKUP) == Order.PICKUP:
        raise ValidationError({"warehouse_id": "Choose the showroom to pick the order up from."})
    warehouse_id, error = pick_fulfilment_warehouse(data["items"])
    if error:
        raise InventoryServiceUnavailable()
    if not warehouse_id:
        raise ValidationError({"items": "No warehouse has all of these items in stock."})
    data["warehouse_id"] = warehouse_id


class CatalogServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Product catalog is unavailable, try again later."
    default_code = "catalog_unavailable"


def _apply_catalog_prices(data):
    """Overwrite each item's price and name with the catalog's: the client never sets what it pays."""
    items = data.get("items", [])
    try:
        catalog = get_catalog_items(item["product_id"] for item in items)
    except CatalogUnavailable:
        raise CatalogServiceUnavailable()
    unknown = sorted({item["product_id"] for item in items} - set(catalog))
    if unknown:
        raise ValidationError({"items": f"Unknown products: {unknown}"})
    for item in items:
        product = catalog[item["product_id"]]
        item["price"] = Decimal(product["price"])
        item["product_name"] = product["name"] or item.get("product_name", "")
        item["warranty_months"] = product.get("warranty_months")
        # Filled in from inventory's weighted-average cost when the stock is written off
        item["cost_price"] = Decimal("0.00")


def _validate_stock(data):
    warehouse_id = data.get("warehouse_id")
    items = data.get("items", [])
    if not warehouse_id or not items:
        return
    for item in items:
        ok, msg = check_availability(
            product_id=item["product_id"],
            warehouse_id=warehouse_id,
            quantity=item["quantity"],
        )
        if ok is False:
            raise ValidationError(
                f"Product {item.get('product_name', item['product_id'])}: {msg}"
            )
        if ok is None:
            logger.warning("Stock check unavailable for product=%s", item["product_id"])


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "order",
            "product_id",
            "product_name",
            "quantity",
            "price",
            # cost_price is the store's purchase cost: never shown to customers
            "warranty_months",
            "created_at",
        ]
        read_only_fields = ["created_at", "order"]


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "channel",
            "status",
            "payment_status",
            "warehouse_id",
            "delivery_method",
            "shipping_city",
            "shipping_address",
            "shipping_cost",
            "customer_name",
            "customer_phone",
            "customer_email",
            "total_amount",
            "item_count",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_item_count(self, obj):
        return sum(item.quantity for item in obj.items.all())


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    promo_code_text = serializers.CharField(source="promo_code.code", read_only=True, default="")

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "channel",
            "status",
            "payment_status",
            "paid_at",
            "delivered_at",
            "warehouse_id",
            "delivery_method",
            "shipping_city",
            "shipping_address",
            "shipping_cost",
            "customer_name",
            "customer_phone",
            "customer_email",
            "total_amount",
            "promo_code_text",
            "discount_amount",
            "notes",
            "items",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class OrderDetailForAdminSerializer(OrderDetailSerializer):
    """Serializer with payment details for admin/staff use only."""

    class Meta(OrderDetailSerializer.Meta):
        fields = OrderDetailSerializer.Meta.fields + ["stripe_session_id", "stripe_payment_intent_id"]


class OrderCreateItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1)
    # Accepted for older clients and ignored: price comes from the catalog, cost from inventory
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)


class OrderCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Order.CHANNEL_CHOICES, default=Order.ONLINE)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    delivery_method = serializers.ChoiceField(choices=Order.DELIVERY_CHOICES, default=Order.PICKUP)
    shipping_city = serializers.CharField(required=False, allow_blank=True, default="")
    shipping_address = serializers.CharField(required=False, allow_blank=True, default="")
    shipping_cost = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default="0.00")
    customer_name = serializers.CharField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(required=False, allow_blank=True, default="")
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    promo_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=40)
    items = OrderCreateItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value

    def validate_warehouse_id(self, value):
        channel = self.initial_data.get("channel", Order.ONLINE)
        if channel == Order.OFFLINE and not value:
            raise serializers.ValidationError(
                "Warehouse is required for offline (POS) orders."
            )
        return value

    def validate(self, data):
        _apply_catalog_prices(data)
        if data.get("channel", Order.ONLINE) == Order.ONLINE:
            _assign_fulfilment_warehouse(data)
        _validate_stock(data)
        return data

class OrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.STATUS_CHOICES)


class POSOrderSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    delivery_method = serializers.ChoiceField(choices=Order.DELIVERY_CHOICES, default=Order.PICKUP)
    shipping_city = serializers.CharField(required=False, allow_blank=True, default="")
    shipping_address = serializers.CharField(required=False, allow_blank=True, default="")
    shipping_cost = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default="0.00")
    # The till records who bought: a warranty or return claim needs a name and a phone to reach them
    customer_name = serializers.CharField(max_length=255)
    customer_phone = serializers.CharField(max_length=32)
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    promo_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=40)
    items = OrderCreateItemSerializer(many=True)

    def validate_customer_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Customer name is required.")
        return value.strip()

    def validate_customer_phone(self, value):
        """Stored as 380XXXXXXXXX, the form online checkout sends; +380…, 380… and 0… are accepted."""
        digits = re.sub(r"\D", "", value)
        if digits.startswith("0"):
            digits = "38" + digits
        if not re.fullmatch(r"380\d{9}", digits):
            raise serializers.ValidationError("Enter a Ukrainian phone number: +380 and 9 digits.")
        return digits

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value

    def validate(self, data):
        _apply_catalog_prices(data)
        _validate_stock(data)
        return data


class ReturnRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    product_id = serializers.IntegerField(source="item.product_id", read_only=True)
    product_name = serializers.CharField(source="item.product_name", read_only=True)
    unit_price = serializers.DecimalField(source="item.price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            "id", "order", "order_number", "item", "product_id", "product_name", "unit_price",
            "quantity", "kind", "reason", "status", "resolution", "refund_amount", "restocked",
            "admin_comment", "created_at", "updated_at",
        ]
        read_only_fields = fields


class ReturnCreateSerializer(serializers.Serializer):
    item_id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ReturnRequest.KIND_CHOICES)
    quantity = serializers.IntegerField(min_value=1, default=1)
    reason = serializers.CharField(max_length=2000)


class ReturnStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ReturnRequest.STATUS_CHOICES)
    resolution = serializers.ChoiceField(choices=ReturnRequest.RESOLUTION_CHOICES, required=False, default="")
    restock = serializers.BooleanField(required=False, allow_null=True, default=None)
    comment = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)
