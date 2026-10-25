import time

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from shared_auth.cache import ResilientRedisCache

from ..eventbus import _adjust_stock
from .helpers import _create_category, _create_product


class CatalogCacheTest(TestCase):
    """Anonymous catalog reads come from the cache until something the catalog shows changes."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.product = _create_product(
            name="Kettle", name_en="Kettle EN", category=_create_category(), stock=10,
        )
        self.url = f"/api/products/{self.product.pk}/"

    def test_repeat_read_skips_the_database(self):
        for url in (self.url, "/api/products/", "/api/categories/"):
            first = self.client.get(url).data
            with self.assertNumQueries(0):
                self.assertEqual(self.client.get(url).data, first)

    def test_stock_event_is_visible_at_once(self):
        """The consumer writes with update(), past model signals; it must still invalidate."""
        self.client.get(self.url)
        _adjust_stock(self.product.pk, -3)
        self.assertEqual(self.client.get(self.url).data["stock"], 7)

    def test_admin_edit_is_visible_at_once(self):
        self.client.get("/api/products/")
        self.product.name = "Kettle 2"
        self.product.save()
        names = [p["name"] for p in self.client.get("/api/products/").data["results"]]
        self.assertEqual(names, ["Kettle 2"])

    def test_locale_is_part_of_the_key(self):
        for lang, name in (("uk", "Kettle"), ("en", "Kettle EN")):
            got = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE=lang).data["name"]
            self.assertEqual(got, name)


class ResilientRedisCacheTest(TestCase):
    """A Redis outage makes the cache miss instead of failing the request."""

    def test_unreachable_redis_behaves_like_an_empty_cache(self):
        down = ResilientRedisCache(
            "redis://127.0.0.1:1/0",
            {"OPTIONS": {"socket_connect_timeout": 0.2, "socket_timeout": 0.2}},
        )
        started = time.monotonic()
        self.assertEqual(down.get("k", "fallback"), "fallback")
        self.assertIsNone(down.set("k", 1, 60))
        self.assertFalse(down.add("k", 1))
        self.assertFalse(down.delete("k"))
        self.assertEqual(down.get_or_set("k", 5), 5)
        self.assertLess(time.monotonic() - started, 5)
