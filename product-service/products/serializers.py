from decimal import Decimal

from django.db.models import Q
from rest_framework import serializers

from .currency import uah_to_usd
from .models import Category, Product

ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def validate_image_file(value):
    if not value:
        return value
    if hasattr(value, "content_type") and value.content_type not in ALLOWED_IMAGE_TYPES:
        raise serializers.ValidationError(
            f"Unsupported image type. Allowed: {', '.join(ALLOWED_IMAGE_TYPES)}"
        )
    if hasattr(value, "size") and value.size > MAX_IMAGE_SIZE:
        raise serializers.ValidationError("Image size must not exceed 5 MB.")
    return value


def _get_locale(request):
    """Extract locale from Accept-Language header. Default: uk."""
    if not request:
        return "uk"
    lang = request.META.get("HTTP_ACCEPT_LANGUAGE", "uk")
    if lang.startswith("en"):
        return "en"
    return "uk"


def _localized(obj, field_base, locale):
    """Return field_base_{locale} if set, fallback to field_base."""
    val = getattr(obj, f"{field_base}_{locale}", "") or getattr(obj, field_base, "")
    return val


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id", "name", "name_uk", "name_en", "parent",
            "image", "image_url", "product_count", "children",
            "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {
            "name": {"required": True, "allow_blank": False, "max_length": 100},
            "name_uk": {"required": False, "allow_blank": True},
            "name_en": {"required": False, "allow_blank": True},
            "parent": {"required": False, "allow_null": True},
        }

    def get_product_count(self, obj):
        # shortcut: one level of subcategories; collect descendant ids if categories nest deeper
        return Product.objects.filter(Q(category=obj) | Q(category__parent=obj)).count()

    def get_children(self, obj):
        depth = self.context.get("category_depth", 0)
        if depth > 5:
            return []
        children = obj.children.all()
        if children:
            ctx = {**self.context, "category_depth": depth + 1}
            return CategorySerializer(children, many=True, context=ctx).data
        return []

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        locale = _get_locale(self.context.get("request"))
        data["name"] = _localized(instance, "name", locale)
        return data

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Category name cannot be blank.")
        return value.strip()

    def validate_image(self, value):
        return validate_image_file(value)


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "slug",
            "name", "name_uk", "name_en",
            "description", "description_uk", "description_en",
            "category", "category_name",
            "price", "original_price",
            "image_url", "stock", "in_stock",
            "features", "specs", "specs_en", "rating", "review_count",
            "brand", "color", "is_on_sale", "warranty_months",
            "created_at", "updated_at",
        ]
        # rating and review_count come from reviews only, stock from the warehouse
        read_only_fields = [
            "slug",
            "stock",
            "in_stock",
            "is_on_sale",
            "rating",
            "review_count",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {"required": True, "allow_blank": False, "max_length": 200},
            "name_uk": {"required": False, "allow_blank": True},
            "name_en": {"required": False, "allow_blank": True},
            "description": {"required": True, "allow_blank": False},
            "description_uk": {"required": False, "allow_blank": True},
            "description_en": {"required": False, "allow_blank": True},
            "price": {"required": True, "min_value": 0},
            "original_price": {"required": False, "min_value": 0},
            "category": {"required": True},
            "features": {"required": False},
            "specs": {"required": False},
            "brand": {"required": False, "allow_blank": True},
            "color": {"required": False, "allow_blank": True},
        }

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url
        return None

    def get_category_name(self, obj):
        locale = _get_locale(self.context.get("request"))
        return _localized(obj.category, "name", locale)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        locale = _get_locale(self.context.get("request"))
        data["name"] = _localized(instance, "name", locale)
        data["description"] = _localized(instance, "description", locale)
        if locale == "en":
            data["specs"] = instance.specs_en or instance.specs
            data["price_usd"] = uah_to_usd(instance.price)
            data["original_price_usd"] = uah_to_usd(instance.original_price)
        return data

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Product name cannot be blank.")
        return value.strip()

    def validate_description(self, value):
        if not value.strip():
            raise serializers.ValidationError("Product description cannot be blank.")
        return value.strip()

    def validate_image(self, value):
        return validate_image_file(value)

    def validate_features(self, value):
        if value is not None:
            if not isinstance(value, list):
                raise serializers.ValidationError("Features must be a list.")
            for item in value:
                if not isinstance(item, str):
                    raise serializers.ValidationError("Each feature must be a string.")
        return value

    def validate_specs(self, value):
        if value is not None and not isinstance(value, dict):
            raise serializers.ValidationError("Specs must be a dictionary.")
        return value

    def validate(self, attrs):
        original_price = attrs.get("original_price")
        if original_price is None:
            original_price = self.instance.original_price if self.instance else Decimal("0")
        price = attrs.get("price")
        if price is None:
            price = self.instance.price if self.instance else None
        if original_price is not None and price is not None and original_price < price:
            raise serializers.ValidationError(
                {"original_price": "Original price cannot be less than current price."}
            )
        attrs["original_price"] = original_price
        return attrs


class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for product list endpoints (e.g. similar products)."""

    category_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "slug",
            "name",
            "name_uk",
            "name_en",
            "category",
            "category_name",
            "price",
            "original_price",
            "image_url",
            "stock",
            "in_stock",
            "rating",
            "review_count",
            "brand",
            "color",
            "is_on_sale",
        ]
        read_only_fields = ["slug", "in_stock", "is_on_sale", "rating", "review_count"]

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url
        return None

    def get_category_name(self, obj):
        locale = _get_locale(self.context.get("request"))
        return _localized(obj.category, "name", locale)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        locale = _get_locale(self.context.get("request"))
        data["name"] = _localized(instance, "name", locale)
        if locale == "en":
            data["price_usd"] = uah_to_usd(instance.price)
            data["original_price_usd"] = uah_to_usd(instance.original_price)
        return data
