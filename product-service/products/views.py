import logging

from django.db import connection
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .catalog_cache import cached_response
from .filters import ProductFilter
from .models import Category, Product
from .pagination import StandardResultsSetPagination
from .serializers import CategorySerializer, ProductListSerializer, ProductSerializer

logger = logging.getLogger(__name__)


class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Product CRUD operations.

    list/retrieve: open (read-only)
    create/update/destroy: requires authentication + admin role
    """

    queryset = Product.objects.select_related("category").all()
    serializer_class = ProductSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = ProductFilter
    search_fields = ["name", "description"]
    ordering_fields = [
        "name",
        "price",
        "stock",
        "created_at",
        "updated_at",
        "in_stock",
        "rating",
        "review_count",
    ]
    ordering = ["-in_stock", "-created_at"]

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)
        # Always sort in-stock products first, out-of-stock last
        return qs.order_by("-in_stock", *qs.query.order_by)

    def get_permissions(self):
        """
        Read-only actions are open; mutating actions require admin auth.
        """
        if self.action in ("list", "retrieve", "low_stock", "by_category", "similar"):
            return [IsAuthenticatedOrReadOnly()]
        return [IsAdminUser()]

    @cached_response
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @cached_response
    def retrieve(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if pk and not pk.isdigit():
            obj = get_object_or_404(Product, slug=pk)
            serializer = self.get_serializer(obj)
            return Response(serializer.data)
        return super().retrieve(request, *args, **kwargs)

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "Product created | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info(
            "Product updated | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )

    def perform_destroy(self, instance):
        logger.warning(
            "Product deleted | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )
        instance.delete()

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Return products whose stock is at or below the given threshold."""
        raw = request.query_params.get("threshold", "10")
        try:
            threshold = int(raw)
        except (TypeError, ValueError) as e:
            raise ValidationError({"threshold": "Must be a non-negative integer."}) from e
        if threshold < 0:
            raise ValidationError({"threshold": "Must be a non-negative integer."})

        products = self.filter_queryset(self.queryset.filter(stock__lte=threshold, in_stock=True))
        page = self.paginate_queryset(products)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    @cached_response
    def by_category(self, request):
        """Return in-stock products for the given category_id."""
        category_id = request.query_params.get("category_id")
        if not category_id:
            raise ValidationError({"category_id": "This parameter is required."})

        try:
            category_id = int(category_id)
        except (TypeError, ValueError) as e:
            raise ValidationError({"category_id": "Must be a positive integer."}) from e
        if category_id <= 0:
            raise ValidationError({"category_id": "Must be a positive integer."})

        # Verify the category actually exists
        if not Category.objects.filter(pk=category_id).exists():
            return Response(
                {"error": "Category not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        products = self.filter_queryset(
            self.queryset.filter(category_id=category_id, in_stock=True)
        )
        page = self.paginate_queryset(products)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    @cached_response
    def similar(self, request, pk=None):
        """Return up to 8 in-stock products in the same category (excluding current)."""
        product = self.get_object()
        if not product.category_id:
            return Response([])

        similar = (
            Product.objects.select_related("category")
            .filter(category=product.category, in_stock=True)
            .exclude(pk=product.pk)[:8]
        )
        serializer = ProductListSerializer(similar, many=True, context={"request": request})
        return Response(serializer.data)


class CategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Category CRUD operations.

    list/retrieve: open (read-only)
    create/update/destroy: requires authentication + admin role
    """

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["name", "parent"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    @cached_response
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @cached_response
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticatedOrReadOnly()]
        return [IsAdminUser()]

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "Category created | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info(
            "Category updated | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )

    def perform_destroy(self, instance):
        # Products and subcategories cascade on delete: refuse instead of wiping them out
        if instance.products.exists() or instance.children.exists():
            raise ValidationError({"error": "category_not_empty"})
        logger.warning(
            "Category deleted | id=%s name=%r user=%s",
            instance.pk,
            instance.name,
            self.request.user,
        )
        instance.delete()


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Health check with database connectivity probe."""
    try:
        connection.ensure_connection()
        return Response(
            {
                "status": "healthy",
                "service": "product-service",
                "checks": {"database": "connected"},
            }
        )
    except Exception:
        logger.exception("Health check failed")
        return Response(
            {
                "status": "unhealthy",
                "service": "product-service",
                "checks": {"database": "disconnected"},
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
