import uuid

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .cart_models import Cart, CartItem
from .cart_serializers import CartSerializer
from .models import Product


@method_decorator(csrf_exempt, name="dispatch")
class CartViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]
    authentication_classes = []  # Bypass SessionAuthentication CSRF check

    MAX_QUANTITY = 999

    @staticmethod
    def _session_id(request):
        """The client's X-Session-Id: a UUID, or a 400 (it used to hit the UUID column and 500)."""
        raw = request.META.get("HTTP_X_SESSION_ID")
        if not raw:
            return None
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            raise ValidationError({"X-Session-Id": "Must be a UUID."}) from None

    def _get_or_create_cart(self, request):
        from django.db import IntegrityError

        user_id = request.META.get("HTTP_X_GATEWAY_USER_ID")
        session_id = self._session_id(request)

        if user_id:
            with transaction.atomic():
                try:
                    cart = Cart.objects.select_for_update().get(user_id=user_id)
                except Cart.MultipleObjectsReturned:
                    # Race-condition duplicates — merge into first, delete rest
                    carts = list(Cart.objects.filter(user_id=user_id).select_for_update())
                    cart = carts[0]
                    for dup in carts[1:]:
                        for item in dup.items.all():
                            existing_item = CartItem.objects.filter(
                                cart=cart, product=item.product
                            ).first()
                            if existing_item:
                                existing_item.quantity += item.quantity
                                existing_item.save()
                            else:
                                item.cart = cart
                                item.save()
                        dup.delete()
                except Cart.DoesNotExist:
                    try:
                        cart = Cart.objects.create(user_id=user_id)
                    except IntegrityError:
                        cart = Cart.objects.select_for_update().get(user_id=user_id)
                if cart.session_id:
                    cart.session_id = None
                    cart.save(update_fields=["session_id"])
                return Cart.objects.prefetch_related("items__product").get(pk=cart.pk)

        if session_id:
            with transaction.atomic():
                try:
                    cart = Cart.objects.select_for_update().get(session_id=session_id)
                except Cart.MultipleObjectsReturned:
                    carts = list(Cart.objects.filter(session_id=session_id).select_for_update())
                    cart = carts[0]
                    for dup in carts[1:]:
                        for item in dup.items.all():
                            existing_item = CartItem.objects.filter(
                                cart=cart, product=item.product
                            ).first()
                            if existing_item:
                                existing_item.quantity += item.quantity
                                existing_item.save()
                            else:
                                item.cart = cart
                                item.save()
                        dup.delete()
                except Cart.DoesNotExist:
                    try:
                        cart = Cart.objects.create(session_id=session_id)
                    except IntegrityError:
                        cart = Cart.objects.select_for_update().get(session_id=session_id)
                return Cart.objects.prefetch_related("items__product").get(pk=cart.pk)

        return None

    def _cart_response(self, cart):
        cart = Cart.objects.prefetch_related("items__product").get(pk=cart.pk)
        return Response(CartSerializer(cart).data)

    def _get_cart_or_error(self, request):
        cart = self._get_or_create_cart(request)
        if not cart:
            return None
        return cart

    def _parse_quantity(self, raw, default=1):
        if raw is None:
            return None
        try:
            qty = int(raw)
        except (TypeError, ValueError):
            return None
        if qty < 1 or qty > self.MAX_QUANTITY:
            return None
        return qty

    def list(self, request):
        cart = self._get_or_create_cart(request)
        if not cart:
            return Response(
                {
                    "id": None,
                    "user_id": None,
                    "session_id": None,
                    "items": [],
                    "subtotal": 0,
                    "item_count": 0,
                    "created_at": None,
                    "updated_at": None,
                }
            )
        serializer = CartSerializer(cart)
        return Response(serializer.data)

    @action(detail=False, methods=["post"])
    def add_item(self, request):
        cart = self._get_cart_or_error(request)
        if not cart:
            return Response(
                {"error": "Authentication or session required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        product_id = request.data.get("product_id")
        quantity = self._parse_quantity(request.data.get("quantity"), 1)
        if quantity is None or quantity < 1:
            return Response(
                {"error": "Quantity must be at least 1."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
        if not created:
            cart_item.quantity += quantity
        else:
            cart_item.quantity = quantity
        cart_item.save()

        return self._cart_response(cart)

    @action(detail=False, methods=["post"])
    def update_item(self, request):
        cart = self._get_cart_or_error(request)
        if not cart:
            return Response(
                {"error": "Authentication or session required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        product_id = request.data.get("product_id")
        quantity = self._parse_quantity(request.data.get("quantity"), 0)

        if not product_id:
            return Response(
                {"error": "product_id is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        if quantity is None or quantity <= 0:
            CartItem.objects.filter(cart=cart, product_id=product_id).delete()
        else:
            CartItem.objects.filter(cart=cart, product_id=product_id).update(quantity=quantity)

        return self._cart_response(cart)

    @action(detail=False, methods=["post"])
    def remove_item(self, request):
        cart = self._get_cart_or_error(request)
        if not cart:
            return Response(
                {"error": "Authentication or session required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        product_id = request.data.get("product_id")
        CartItem.objects.filter(cart=cart, product_id=product_id).delete()

        return self._cart_response(cart)

    @action(detail=False, methods=["post"])
    def clear(self, request):
        cart = self._get_cart_or_error(request)
        if not cart:
            return Response(
                {"error": "Authentication or session required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        cart.items.all().delete()
        return self._cart_response(cart)

    @action(detail=False, methods=["post"])
    def merge(self, request):
        session_id = self._session_id(request)
        user_id = request.META.get("HTTP_X_GATEWAY_USER_ID")

        if not user_id or not session_id:
            return Response(
                {"error": "Both session and authenticated user required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                session_cart = Cart.objects.select_for_update().get(session_id=session_id)
        except Cart.DoesNotExist:
            user_cart = self._get_or_create_cart(request)
            return Response(CartSerializer(user_cart).data)

        user_cart = self._get_or_create_cart(request)

        with transaction.atomic():
            for session_item in session_cart.items.all():
                user_item, created = CartItem.objects.get_or_create(
                    cart=user_cart, product=session_item.product
                )
                if not created:
                    user_item.quantity += session_item.quantity
                else:
                    user_item.quantity = session_item.quantity
                user_item.save()

            session_cart.items.all().delete()
            session_cart.delete()

        return self._cart_response(user_cart)
