
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from ..cart_models import Cart, CartItem
from .helpers import _create_category, _create_product

User = get_user_model()


class CartAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = _create_category()
        self.product = _create_product(category=self.cat)
        self.user_id = "test-user-123"
        self.user = User.objects.create_user(
            username=f"gw_{self.user_id}",
            email=f"{self.user_id}@gateway.local",
        )
        self.auth_headers = {
            "HTTP_X_GATEWAY_USER_ID": self.user_id,
            "HTTP_X_GATEWAY_USER_ROLE": "user",
        }
        self.session_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def _cart_url(self) -> str:
        return "/api/cart/"

    def test_get_cart_creates_new_cart(self):
        self.assertEqual(Cart.objects.count(), 0)
        response = self.client.get(self._cart_url(), **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Cart.objects.count(), 1)

    def test_get_cart_without_auth(self):
        response = self.client.get(self._cart_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["items"], [])

    def test_get_cart_returns_items(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        response = self.client.get(self._cart_url(), **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["product_name"], self.product.name)
        self.assertEqual(response.data["items"][0]["quantity"], 2)

    def test_add_item(self):
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": self.product.pk, "quantity": 3},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cart = Cart.objects.get(user_id=self.user_id)
        self.assertEqual(cart.items.count(), 1)
        item = cart.items.first()
        self.assertEqual(item.quantity, 3)

    def test_add_item_without_auth(self):
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": self.product.pk, "quantity": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_add_item_nonexistent_product(self):
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": 99999, "quantity": 1},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_item_no_product_id(self):
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"quantity": 1},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_item_twice_increments_quantity(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)

        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": self.product.pk, "quantity": 2},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = CartItem.objects.get(cart=cart, product=self.product)
        self.assertEqual(item.quantity, 3)

    def test_cart_total(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        response = self.client.get(self._cart_url(), **self.auth_headers)
        self.assertAlmostEqual(float(response.data["subtotal"]), 199.98, places=2)
        self.assertEqual(response.data["item_count"], 2)

    def test_add_item_zero_quantity_not_allowed(self):
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": self.product.pk, "quantity": 0},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_item_changes_quantity(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        response = self.client.post(
            f"{self._cart_url()}update_item/",
            {"product_id": self.product.pk, "quantity": 5},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = CartItem.objects.get(cart=cart, product=self.product)
        self.assertEqual(item.quantity, 5)

    def test_update_item_removes_when_quantity_zero(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        response = self.client.post(
            f"{self._cart_url()}update_item/",
            {"product_id": self.product.pk, "quantity": 0},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.filter(cart=cart).count(), 0)

    def test_update_item_without_auth(self):
        response = self.client.post(
            f"{self._cart_url()}update_item/",
            {"product_id": self.product.pk, "quantity": 3},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_remove_item(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        response = self.client.post(
            f"{self._cart_url()}remove_item/",
            {"product_id": self.product.pk},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.filter(cart=cart).count(), 0)

    def test_remove_item_nonexistent(self):
        cart = Cart.objects.create(user_id=self.user_id)
        response = self.client.post(
            f"{self._cart_url()}remove_item/",
            {"product_id": self.product.pk},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.filter(cart=cart).count(), 0)

    def test_clear_removes_all_items(self):
        cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        response = self.client.post(
            f"{self._cart_url()}clear/",
            {},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.filter(cart=cart).count(), 0)

    def test_merge_transfers_session_items_to_user_cart(self):
        session_id = self.session_id
        session_cart = Cart.objects.create(session_id=session_id)
        CartItem.objects.create(cart=session_cart, product=self.product, quantity=3)
        headers = {
            **self.auth_headers,
            "HTTP_X_SESSION_ID": session_id,
        }
        response = self.client.post(
            f"{self._cart_url()}merge/",
            {},
            format="json",
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user_cart = Cart.objects.get(user_id=self.user_id)
        self.assertEqual(user_cart.items.count(), 1)
        self.assertEqual(user_cart.items.first().quantity, 3)
        self.assertFalse(Cart.objects.filter(session_id=session_id).exists())

    def test_merge_increments_existing_user_cart_item(self):
        session_id = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        session_cart = Cart.objects.create(session_id=session_id)
        CartItem.objects.create(cart=session_cart, product=self.product, quantity=2)
        user_cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(cart=user_cart, product=self.product, quantity=1)
        headers = {
            **self.auth_headers,
            "HTTP_X_SESSION_ID": session_id,
        }
        response = self.client.post(
            f"{self._cart_url()}merge/",
            {},
            format="json",
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = CartItem.objects.get(cart=user_cart, product=self.product)
        self.assertEqual(item.quantity, 3)

    def test_merge_requires_both_session_and_user(self):
        response = self.client.post(
            f"{self._cart_url()}merge/",
            {},
            format="json",
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_session_cart_create_and_add(self):
        session_id = self.session_id
        response = self.client.get(
            self._cart_url(),
            **{"HTTP_X_SESSION_ID": session_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session_id"], session_id)
        response = self.client.post(
            f"{self._cart_url()}add_item/",
            {"product_id": self.product.pk, "quantity": 2},
            format="json",
            **{"HTTP_X_SESSION_ID": session_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cart = Cart.objects.get(session_id=session_id)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().quantity, 2)

    def test_session_cart_reuses_existing(self):
        session_id = "c3d4e5f6-a7b8-9012-cdef-123456789012"
        cart = Cart.objects.create(session_id=session_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=4)
        response = self.client.get(
            self._cart_url(),
            **{"HTTP_X_SESSION_ID": session_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["quantity"], 4)

    def test_session_cart_not_shared_with_user_cart(self):
        session_id = "d4e5f6a7-b8c9-0123-defa-234567890123"
        cart = Cart.objects.create(session_id=session_id)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        response = self.client.get(
            self._cart_url(),
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 0)


class CartSessionIdTest(APITestCase):
    """X-Session-Id comes from the client: a value that is not a UUID is a 400, never a 500."""

    def test_malformed_session_id_is_rejected(self):
        for path in ("/api/cart/", "/api/cart/add_item/", "/api/cart/merge/"):
            method = self.client.get if path == "/api/cart/" else self.client.post
            response = method(path, {"product_id": 1, "quantity": 1}, format="json",
                              HTTP_X_SESSION_ID="k7-rbac-session", HTTP_X_GATEWAY_USER_ID="u1")
            self.assertEqual(response.status_code, 400, path)
