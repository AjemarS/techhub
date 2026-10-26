from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from ..models import Category, Product
from .helpers import _create_admin_user, _create_category, _create_product, _create_regular_user

User = get_user_model()


class ProductAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = _create_category()
        self.product = _create_product(category=self.cat)
        self.list_url = "/api/products/"
        self.detail_url = f"/api/products/{self.product.pk}/"

    def test_list_products(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)

    def test_list_returns_category_name(self):
        response = self.client.get(self.list_url)
        result = response.data["results"][0]
        self.assertIn("category_name", result)
        self.assertEqual(result["category_name"], self.cat.name)

    def test_list_pagination_default(self):
        for i in range(25):
            _create_product(category=self.cat, name=f"Product {i}")
        response = self.client.get(self.list_url)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertEqual(response.data["count"], 26)

    def test_retrieve_product(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], self.product.name)

    def test_retrieve_nonexistent(self):
        response = self.client.get("/api/products/99999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_requires_admin(self):
        user = _create_regular_user()
        self.client.force_authenticate(user)
        payload = {
            "name": "New",
            "description": "Desc",
            "category": self.cat.pk,
            "price": "10.00",
        }
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_as_admin(self):
        admin = _create_admin_user()
        self.client.force_authenticate(admin)
        payload = {
            "name": "Admin Created",
            "description": "Created by admin",
            "category": self.cat.pk,
            "price": "29.99",
            "original_price": "39.99",
        }
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.count(), 2)

    def test_update_requires_admin(self):
        user = _create_regular_user()
        self.client.force_authenticate(user)
        response = self.client.patch(self.detail_url, {"name": "Hacked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_as_admin(self):
        admin = _create_admin_user()
        self.client.force_authenticate(admin)
        response = self.client.patch(self.detail_url, {"name": "Updated"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated")

    def test_delete_requires_admin(self):
        user = _create_regular_user()
        self.client.force_authenticate(user)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_as_admin(self):
        admin = _create_admin_user()
        self.client.force_authenticate(admin)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Product.objects.count(), 0)

    def test_filter_by_category(self):
        cat2 = _create_category("Other")
        _create_product(category=cat2, name="Other Product")
        response = self.client.get(self.list_url, {"category": self.cat.pk})
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.product.pk)

    def test_filter_by_ids(self):
        second = _create_product(category=self.cat, name="Second")
        _create_product(category=self.cat, name="Not asked")
        response = self.client.get(self.list_url, {"ids": f"{self.product.pk},{second.pk}"})
        self.assertEqual({r["id"] for r in response.data["results"]}, {self.product.pk, second.pk})

    def test_filter_by_name(self):
        _create_product(category=self.cat, name="Unique Widget")
        response = self.client.get(self.list_url, {"search": "Unique"})
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Unique Widget")

    def test_filter_by_price_range(self):
        _create_product(category=self.cat, name="Cheap", price=Decimal("5.00"))
        _create_product(category=self.cat, name="Expensive", price=Decimal("500.00"))
        response = self.client.get(self.list_url, {"min_price": "10", "max_price": "100"})
        for r in response.data["results"]:
            price = Decimal(r["price"])
            self.assertGreaterEqual(price, Decimal("10"))
            self.assertLessEqual(price, Decimal("100"))

    def test_low_stock(self):
        _create_product(category=self.cat, name="Low", stock=3)
        response = self.client.get("/api/products/low_stock/", {"threshold": "5"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertTrue(all(Decimal(r["stock"]) <= 5 for r in results))

    def test_low_stock_invalid_threshold(self):
        response = self.client.get("/api/products/low_stock/", {"threshold": "-1"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_by_category(self):
        cat2 = _create_category("Target")
        _create_product(category=cat2, name="In Target Cat", stock=10)
        response = self.client.get("/api/products/by_category/", {"category_id": cat2.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "In Target Cat")

    def test_by_category_nonexistent(self):
        response = self.client.get("/api/products/by_category/", {"category_id": 99999})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_by_category_missing_param(self):
        response = self.client.get("/api/products/by_category/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stock_follows_the_warehouse_only(self):
        """Product.stock moves with inventory events; set by hand it would drift (fix #35)."""
        admin = _create_admin_user()
        self.client.force_authenticate(admin)
        url = f"/api/products/{self.product.pk}/"
        response = self.client.post(f"{url}update_stock/", {"quantity": 5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response = self.client.patch(url, {"stock": 999}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

class LocaleSerializationTest(APITestCase):
    def setUp(self):
        self.cat = Category.objects.create(
            name="Електроніка",
            name_uk="Електроніка",
            name_en="Electronics",
        )
        self.product = Product.objects.create(
            name="Телефон",
            name_uk="Телефон",
            name_en="Phone",
            description="Опис українською",
            description_uk="Опис українською",
            description_en="English description",
            category=self.cat,
            price=Decimal("1000.00"),
            original_price=Decimal("1200.00"),
            stock=10,
        )

    def test_default_locale_uses_ukrainian(self):
        response = self.client.get(f"/api/products/{self.product.pk}/")
        self.assertEqual(response.data["name"], "Телефон")
        self.assertEqual(response.data["description"], "Опис українською")
        self.assertNotIn("price_usd", response.data)

    def test_english_locale_uses_english_fields(self):
        response = self.client.get(
            f"/api/products/{self.product.pk}/",
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.data["name"], "Phone")
        self.assertEqual(response.data["description"], "English description")

    def test_english_locale_includes_usd_prices(self):
        response = self.client.get(
            f"/api/products/{self.product.pk}/",
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertIn("price_usd", response.data)
        self.assertGreater(float(response.data["price_usd"]), 0)
        self.assertLess(float(response.data["price_usd"]), 1000.0)
        self.assertIn("original_price_usd", response.data)

    def test_locale_falls_back_to_base_when_variant_empty(self):
        cat = Category.objects.create(name="Base Cat", name_en="", name_uk="")
        product = Product.objects.create(
            name="Base Only",
            name_en="",
            name_uk="",
            description="Base description",
            description_en="",
            description_uk="",
            category=cat,
            price=Decimal("50.00"),
            original_price=Decimal("60.00"),
            stock=5,
        )
        response = self.client.get(
            f"/api/products/{product.pk}/",
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.data["name"], "Base Only")
        self.assertEqual(response.data["description"], "Base description")

    def test_category_locale_in_list(self):
        response = self.client.get(
            "/api/categories/",
            HTTP_ACCEPT_LANGUAGE="en",
        )
        cat_data = next(c for c in response.data["results"] if c["id"] == self.cat.pk)
        self.assertEqual(cat_data["name"], "Electronics")

    def test_category_locale_default_uk(self):
        response = self.client.get("/api/categories/")
        cat_data = next(c for c in response.data["results"] if c["id"] == self.cat.pk)
        self.assertEqual(cat_data["name"], "Електроніка")


class CategoryChildrenTest(APITestCase):
    def test_category_children_serialization(self):
        parent = Category.objects.create(name="Parent")
        Category.objects.create(name="Child 1", parent=parent)
        Category.objects.create(name="Child 2", parent=parent)
        response = self.client.get(f"/api/categories/{parent.pk}/")
        self.assertIn("children", response.data)
        self.assertEqual(len(response.data["children"]), 2)
        child_names = {c["name"] for c in response.data["children"]}
        self.assertEqual(child_names, {"Child 1", "Child 2"})

    def test_category_no_children(self):
        parent = Category.objects.create(name="Leaf")
        response = self.client.get(f"/api/categories/{parent.pk}/")
        self.assertEqual(response.data["children"], [])


@override_settings(ALLOWED_HOSTS=["shop.example"])
class PublicLinksTest(APITestCase):
    """Pagination links name the public host and scheme the gateway saw, not the internal one."""

    def test_next_link_uses_forwarded_host_and_scheme(self):
        category = _create_category()
        for i in range(21):
            _create_product(name=f"P{i}", category=category)
        response = self.client.get(
            "/api/products/", HTTP_X_FORWARDED_HOST="shop.example", HTTP_X_FORWARDED_PROTO="https",
        )
        link = response.data["next"]
        self.assertTrue(link.startswith("https://shop.example/api/products/?"), link)


@override_settings(INTERNAL_SERVICE_TOKEN="s3cret")
class InternalServiceThrottleTest(APITestCase):
    """Server-side rendering calls from one address for all visitors: its token is not throttled."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.rates = mock.patch("rest_framework.throttling.SimpleRateThrottle.THROTTLE_RATES",
                                {"anon": "2/minute", "user": "2/minute"})
        self.rates.start()
        self.addCleanup(self.rates.stop)

    def _codes(self, n, **headers):
        return [self.client.get("/api/categories/", **headers).status_code for _ in range(n)]

    def test_anonymous_client_is_throttled(self):
        self.assertEqual(self._codes(3), [200, 200, 429])

    def test_service_token_is_not_throttled_but_a_wrong_one_is(self):
        self.assertEqual(self._codes(5, HTTP_X_INTERNAL_TOKEN="s3cret"), [200] * 5)
        self.assertEqual(self._codes(3, HTTP_X_INTERNAL_TOKEN="guess"), [200, 200, 429])
