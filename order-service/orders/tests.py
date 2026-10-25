import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from . import order_service as svc
from .models import Order, OrderItem, OrderStatusHistory, PromoCode, ReturnRequest

User = get_user_model()

# Inventory is a separate service: unit tests stub its HTTP client instead of depending on a live stack
INVENTORY_OK = ({"ok": True}, None)

CATALOG_PRICE = Decimal("199.99")


def _fake_catalog(product_ids):
    return {pid: {"price": str(CATALOG_PRICE), "name": f"Catalog product {pid}", "warranty_months": 24} for pid in product_ids}


# The catalog is product-service: every order created over the API gets its price from this stub
_catalog_patch = mock.patch("orders.serializers.get_catalog_items", side_effect=_fake_catalog)
# The stock pre-check calls inventory too: unstubbed, the result hung on whatever the live stack held
_availability_patch = mock.patch("orders.serializers.check_availability", return_value=(True, None))


def _stub_inventory(target):
    """For tests about orders rather than stock: every inventory call succeeds (a test class or method)."""
    target = mock.patch("orders.views.check_availability", new=mock.Mock(return_value=(True, None)))(target)
    for name in ("reserve_stock", "release_stock", "deduct_stock_batch", "return_stock"):
        target = mock.patch(f"orders.order_service.{name}", new=mock.Mock(return_value=INVENTORY_OK))(target)
    return target


def setUpModule():
    _catalog_patch.start()
    _availability_patch.start()


def tearDownModule():
    _catalog_patch.stop()
    _availability_patch.stop()


def _create_admin_user():
    return User.objects.create_superuser("admin", "admin@test.com", "password123")


def _create_regular_user():
    return User.objects.create_user("user", "user@test.com", "password123")


def _create_order(status=Order.UNPAID, channel=Order.ONLINE, **kwargs):
    data = {
        "order_number": "ORD-000001",
        "channel": channel,
        "status": status,
        "customer_name": "Test Customer",
        "customer_email": "customer@test.com",
        "total_amount": Decimal("299.99"),
        "created_by": "gw_1",
    }
    data.update(kwargs)
    return Order.objects.create(**data)


def _create_order_item(order, product_id=1, quantity=1, price=Decimal("99.99")):
    return OrderItem.objects.create(
        order=order,
        product_id=product_id,
        product_name=f"Product {product_id}",
        quantity=quantity,
        price=price,
        cost_price=Decimal("50.00"),
    )


class OrderModelTest(TestCase):
    def test_create_order(self):
        order = _create_order()
        self.assertEqual(str(order), "Order #ORD-000001")
        self.assertEqual(order.status, Order.UNPAID)

    def test_status_transitions(self):
        order = _create_order()
        self.assertTrue(order.can_transition_to(Order.PAID))
        self.assertTrue(order.can_transition_to(Order.CANCELLED))
        self.assertFalse(order.can_transition_to(Order.DELIVERING))

        order.status = Order.PAID
        self.assertTrue(order.can_transition_to(Order.DELIVERING))
        self.assertTrue(order.can_transition_to(Order.CANCELLED))

        order.status = Order.DELIVERING
        self.assertTrue(order.can_transition_to(Order.DELIVERED))
        self.assertTrue(order.can_transition_to(Order.CANCELLED))

        order.status = Order.DELIVERED
        self.assertTrue(order.can_transition_to(Order.COMPLETED))
        self.assertFalse(order.can_transition_to(Order.DELIVERING))

    def test_order_number_unique(self):
        _create_order()
        with self.assertRaises(IntegrityError):
            _create_order()

    def test_order_total_amount(self):
        order = _create_order(total_amount=Decimal("199.98"))
        _create_order_item(order, product_id=1, quantity=2, price=Decimal("99.99"))
        self.assertEqual(order.total_amount, Decimal("199.98"))

    def test_channel_choices(self):
        online = _create_order(channel=Order.ONLINE)
        offline = _create_order(
            order_number="ORD-000002", channel=Order.OFFLINE
        )
        self.assertEqual(online.channel, Order.ONLINE)
        self.assertEqual(offline.channel, Order.OFFLINE)


class OrderItemModelTest(TestCase):
    def test_create_item(self):
        order = _create_order()
        item = _create_order_item(order)
        self.assertEqual(str(item), "Product 1 x1")
        self.assertEqual(item.price, Decimal("99.99"))

    def test_item_belongs_to_order(self):
        order = _create_order()
        _create_order_item(order, product_id=1)
        _create_order_item(order, product_id=2, quantity=3)
        self.assertEqual(order.items.count(), 2)


class OrderAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()

    def test_list_orders_empty(self):
        response = self.client.get("/api/orders/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @_stub_inventory
    def test_create_order_unauthenticated(self):
        response = self.client.post(
            "/api/orders/",
            {
                "warehouse_id": 1,
                "items": [
                    {"product_id": 1, "quantity": 1, "price": "99.99"}
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @_stub_inventory
    def test_create_order_admin(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/orders/",
            {
                "channel": "online",
                "warehouse_id": 1,
                "customer_name": "John",
                "customer_email": "john@test.com",
                "items": [
                    {"product_id": 1, "quantity": 2, "price": "99.99",
                     "product_name": "Test Product"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data
        self.assertEqual(data["channel"], "online")
        self.assertEqual(data["status"], "unpaid")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(Decimal(data["total_amount"]), CATALOG_PRICE * 2)  # client sent 99.99: ignored

    @mock.patch("orders.order_service.reserve_stock", return_value=INVENTORY_OK)
    @mock.patch("orders.serializers.check_availability", return_value=(True, None))
    @mock.patch("orders.views.check_availability", return_value=(True, None))
    def test_create_order_with_warehouse(self, _check_view, _check_serializer, reserve):
        """Order with warehouse_id reserves every item."""
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/orders/",
            {
                "channel": "online",
                "warehouse_id": 1,
                "customer_name": "John",
                "customer_email": "john@test.com",
                "items": [
                    {"product_id": 1, "quantity": 2, "price": "99.99",
                     "product_name": "Test Product"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data
        self.assertEqual(data["channel"], "online")
        self.assertEqual(data["status"], "unpaid")
        self.assertEqual(data["payment_status"], "unpaid")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(Decimal(data["total_amount"]), CATALOG_PRICE * 2)  # client sent 99.99: ignored
        reserve.assert_called_once()
        self.assertEqual(reserve.call_args.kwargs["quantity"], 2)

    def test_create_order_empty_items(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/orders/",
            {"items": []},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_order_status_transition_valid(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        order = _create_order()
        response = self.client.patch(
            f"/api/orders/{order.pk}/status/",
            {"status": "paid"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PAID)

    def test_order_status_transition_invalid(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        order = _create_order(status=Order.COMPLETED)
        response = self.client.patch(
            f"/api/orders/{order.pk}/status/",
            {"status": "cancelled"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_my_orders(self):
        user = _create_regular_user()
        self.client.force_authenticate(user=user)
        _create_order(created_by=str(user.id))
        _create_order(order_number="ORD-000002", created_by="other_user")
        response = self.client.get("/api/orders/my/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)


class POSAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()

    def _auth_admin(self):
        user = User.objects.create_superuser("admin", "admin@test.com", "password123")
        self.client.force_authenticate(user=user)
        return user

    @mock.patch("orders.order_service.deduct_stock_batch", return_value=INVENTORY_OK)
    def test_pos_order(self, deduct):
        self._auth_admin()
        response = self.client.post(
            "/api/orders/pos/",
            {
                "warehouse_id": 1,
                "customer_name": "POS Customer",
                "customer_phone": "+380 67 123 45 67",
                "items": [
                    {"product_id": 1, "quantity": 1, "price": "199.99"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["channel"], Order.OFFLINE)
        self.assertEqual(response.data["status"], Order.COMPLETED)
        self.assertEqual(response.data["payment_status"], Order.PAYMENT_PAID)
        deduct.assert_called_once()

    def test_pos_requires_name_and_ukrainian_phone(self):
        self._auth_admin()
        base = {"warehouse_id": 1, "items": [{"product_id": 1, "quantity": 1, "price": "199.99"}]}
        for extra in ({}, {"customer_name": " ", "customer_phone": "0671234567"},
                      {"customer_name": "Ann", "customer_phone": "12345"}):
            response = self.client.post("/api/orders/pos/", {**base, **extra}, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, extra)
        self.assertFalse(Order.objects.exists())

    def test_pos_order_without_warehouse_fails(self):
        self._auth_admin()
        response = self.client.post(
            "/api/orders/pos/",
            {
                "customer_name": "POS Customer",
                "items": [
                    {"product_id": 1, "quantity": 1, "price": "199.99"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch("orders.order_service.deduct_stock_batch", return_value=INVENTORY_OK)
    def test_status_transition_paid_to_delivering(self, deduct):
        """Status to delivering triggers deduct saga."""
        self._auth_admin()
        order = _create_order(status=Order.PAID, warehouse_id=1)
        _create_order_item(order, product_id=1, quantity=3)
        response = self.client.patch(
            f"/api/orders/{order.pk}/status/",
            {"status": "delivering"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.DELIVERING)
        self.assertEqual(deduct.call_args.kwargs["items"], [{"product_id": 1, "quantity": 3}])
        self.assertTrue(deduct.call_args.kwargs["from_reserved"])

    @mock.patch("orders.order_service.release_stock", return_value=(None, "inventory unreachable"))
    def test_create_order_cancel_releases_stock(self, release):
        """Cancelling an order triggers release saga (fails gracefully when inventory unreachable)."""
        self._auth_admin()
        order = _create_order(order_number="ORD-CANCEL-001", warehouse_id=1)
        _create_order_item(order)
        response = self.client.patch(
            f"/api/orders/{order.pk}/status/",
            {"status": "cancelled"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)
        release.assert_called_once()


class ReportsAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()

    def test_sales_report_unauthenticated(self):
        response = self.client.get("/api/reports/sales/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sales_report_admin(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/reports/sales/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_revenue_report_admin(self):
        user = _create_admin_user()
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/reports/revenue/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ReportNumbersTest(APITestCase):
    """Every report counts the same orders and the same revenue."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=_create_admin_user())
        self.today = timezone.localdate()

    def _order(self, number, status, channel=Order.ONLINE, days_ago=0, lines=((2, "100.00", "60.00"),)):
        order = _create_order(
            order_number=number, status=status, channel=channel,
            total_amount=Decimal("999.00"),  # includes shipping: must not leak into revenue
        )
        for qty, price, cost in lines:
            OrderItem.objects.create(order=order, product_id=1, quantity=qty, price=Decimal(price), cost_price=Decimal(cost))
        created = timezone.make_aware(datetime.combine(self.today - timedelta(days=days_ago), time(12)))
        Order.objects.filter(pk=order.pk).update(created_at=created)
        return order

    def test_sales_revenue_and_daily_agree(self):
        self._order("A", Order.PAID)
        self._order("B", Order.COMPLETED, channel=Order.OFFLINE, lines=((1, "50.00", "20.00"),))
        self._order("C", Order.UNPAID)
        self._order("D", Order.CANCELLED)

        sales = self.client.get("/api/reports/sales/").data
        revenue = self.client.get("/api/reports/revenue/").data
        daily = self.client.get("/api/reports/daily-sales/").data["daily"]

        self.assertEqual(sales["total_orders"], 2)
        self.assertEqual(sales["total_revenue"], 250.0)
        self.assertEqual(sales["total_margin"], 110.0)
        self.assertEqual(sales["margin_percent"], 44.0)
        self.assertEqual(revenue["total_revenue"], sales["total_revenue"])
        self.assertEqual(revenue["order_count"], sales["total_orders"])
        self.assertEqual(sum(d["revenue"] for d in daily), sales["total_revenue"])
        self.assertEqual({c["channel"]: c["revenue"] for c in sales["by_channel"]}, {"online": 200.0, "offline": 50.0})

    def test_date_only_to_includes_whole_day(self):
        self._order("A", Order.PAID)
        data = self.client.get(f"/api/reports/sales/?to={self.today.isoformat()}").data
        self.assertEqual(data["total_orders"], 1)

    def test_from_excludes_older_orders(self):
        self._order("OLD", Order.PAID, days_ago=10)
        self._order("NEW", Order.PAID)
        frm = (self.today - timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(f"/api/reports/sales/?from={frm}").data["total_orders"], 1)

    def test_invalid_date_is_rejected(self):
        self.assertEqual(self.client.get("/api/reports/sales/?from=yesterday").status_code, status.HTTP_400_BAD_REQUEST)

    def test_daily_is_zero_filled(self):
        self._order("A", Order.PAID, days_ago=2)
        frm = (self.today - timedelta(days=3)).isoformat()
        daily = self.client.get(f"/api/reports/daily-sales/?from={frm}&to={self.today.isoformat()}").data["daily"]
        self.assertEqual([d["orders"] for d in daily], [0, 1, 0, 0])

    def test_previous_period_and_kpis(self):
        self._order("NOW", Order.PAID)                                       # revenue 200
        self._order("NOW-X", Order.CANCELLED)
        self._order("PREV", Order.PAID, days_ago=8, lines=((1, "50.00", "20.00"),))
        self._order("OLDER", Order.PAID, days_ago=20)
        frm, to = (self.today - timedelta(days=6)).isoformat(), self.today.isoformat()
        data = self.client.get(f"/api/reports/sales/?from={frm}&to={to}").data
        self.assertEqual(data["average_order_value"], 200.0)
        self.assertEqual(data["cancelled_orders"], 1)
        self.assertEqual(data["cancellation_rate"], 50.0)
        self.assertEqual(data["previous"]["total_revenue"], 50.0)            # days 7..13 ago only
        self.assertIsNone(self.client.get("/api/reports/sales/").data["previous"])

    def test_top_products_sorted_and_shared(self):
        order = self._order("A", Order.PAID, lines=())
        OrderItem.objects.create(order=order, product_id=1, product_name="Fridge", quantity=1, price=Decimal("300.00"), cost_price=Decimal("280.00"))
        OrderItem.objects.create(order=order, product_id=2, product_name="Kettle", quantity=5, price=Decimal("20.00"), cost_price=Decimal("5.00"))
        by_revenue = self.client.get("/api/reports/top-products/").data["products"]
        self.assertEqual([p["product_name"] for p in by_revenue], ["Fridge", "Kettle"])
        self.assertEqual(by_revenue[0]["revenue_share"], 75.0)
        by_margin = self.client.get("/api/reports/top-products/?sort=margin&limit=1").data["products"]
        self.assertEqual([(p["product_name"], p["margin"]) for p in by_margin], [("Kettle", 75.0)])
        self.assertEqual(self.client.get("/api/reports/top-products/?sort=price").status_code, 400)

    def test_abc_classes_follow_cumulative_revenue(self):
        order = self._order("A", Order.PAID, lines=())
        for pid, price in [(1, "700.00"), (2, "150.00"), (3, "100.00"), (4, "50.00")]:
            OrderItem.objects.create(order=order, product_id=pid, product_name=f"P{pid}", quantity=1, price=Decimal(price), cost_price=Decimal("1.00"))
        data = self.client.get("/api/reports/abc/").data
        # cumulative share before each product: 0, 70, 85, 95
        self.assertEqual([p["abc_class"] for p in data["products"]], ["A", "A", "B", "C"])
        self.assertEqual(data["products"][-1]["cumulative_share"], 100.0)
        self.assertEqual(data["classes"]["A"], {"products": 2, "revenue": 850.0, "revenue_share": 85.0})
        self.assertEqual(data["classes"]["C"]["products"], 1)

    def _history(self, order, steps, start_hours_ago):
        """steps: [(to_status, hours after creation)]; rewrites timestamps to fixed offsets."""
        base = timezone.now() - timedelta(hours=start_hours_ago)
        Order.objects.filter(pk=order.pk).update(created_at=base)
        prev = Order.UNPAID
        for to, hours in steps:
            row = OrderStatusHistory.objects.create(order=order, from_status=prev, to_status=to)
            OrderStatusHistory.objects.filter(pk=row.pk).update(changed_at=base + timedelta(hours=hours))
            prev = to

    def test_fulfillment_funnel_durations_and_cancel_stage(self):
        shipped = self._order("S", Order.DELIVERED)
        self._history(shipped, [(Order.PAID, 2), (Order.DELIVERING, 26), (Order.DELIVERED, 74)], 100)
        paid = self._order("P", Order.PAID)
        self._history(paid, [(Order.PAID, 4)], 10)
        dropped = self._order("X", Order.CANCELLED)
        self._history(dropped, [(Order.PAID, 1), (Order.CANCELLED, 3)], 10)
        self._order("U", Order.UNPAID)
        self._order("POS", Order.COMPLETED, channel=Order.OFFLINE)

        data = self.client.get("/api/reports/fulfillment/").data
        self.assertEqual(data["orders"], 4)
        self.assertEqual([f["count"] for f in data["funnel"]], [4, 3, 1, 1, 0])
        self.assertEqual(data["payment_conversion"], 75.0)
        self.assertEqual(data["cancelled"]["by_stage"]["paid"], 1)
        to_paid = data["durations"][0]
        self.assertEqual((to_paid["count"], to_paid["median_hours"]), (3, 2.0))
        self.assertEqual(data["durations"][1]["avg_hours"], 24.0)

    def test_status_change_is_recorded(self):
        order = _create_order(status=Order.UNPAID)
        svc.update_order_status(order, Order.PAID)
        self.assertEqual(
            list(order.status_history.values_list("from_status", "to_status")), [(Order.UNPAID, Order.PAID)]
        )

    def test_monthly_grouping_zero_fills_months(self):
        self._order("A", Order.PAID)
        first_of_month = self.today.replace(day=1)
        frm = (first_of_month - timedelta(days=40)).isoformat()
        data = self.client.get(f"/api/reports/daily-sales/?group=month&from={frm}&to={self.today.isoformat()}").data
        self.assertEqual(data["group"], "month")
        self.assertEqual(len(data["daily"]), 3)
        self.assertTrue(all(d["date"].endswith("-01") for d in data["daily"]))
        self.assertEqual([d["orders"] for d in data["daily"]], [0, 0, 1])
        self.assertEqual(data["daily"][-1]["revenue"], 200.0)

    @mock.patch("orders.reports.get_stock_valuation", return_value=(None, "connection refused"))
    def test_inventory_value_reports_unreachable_inventory(self, _):
        self.assertEqual(self.client.get("/api/reports/inventory-value/").status_code, 503)


class _FakeUrlopenResponse:
    """Minimal urllib response double: context manager exposing read()."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class DeliveryApiTests(APITestCase):
    """Coverage for delivery proxy endpoints (Nova Poshta, Ukrposhta)."""

    NOVA_POSHTA_URL = "/api/orders/delivery/nova-poshta-warehouses/"
    UKRPOSHTA_URL = "/api/orders/delivery/ukrposhta-warehouses/"

    def setUp(self):
        self.client = APIClient()

    # --- Nova Poshta: mock path (no API key configured) ---

    @override_settings(NOVA_POSHTA_API_KEY="")
    def test_nova_poshta_mock_path_returns_warehouse_shape(self):
        response = self.client.post(
            self.NOVA_POSHTA_URL,
            {"city_name": "Київ"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()["data"]
        self.assertEqual(len(data), 3)
        for item in data:
            self.assertEqual(set(item.keys()), {"name", "ref", "address"})
            self.assertTrue(item["name"])
            self.assertTrue(item["ref"])
            self.assertTrue(item["address"])

    def test_nova_poshta_requires_city_name(self):
        response = self.client.post(self.NOVA_POSHTA_URL, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nova_poshta_rejects_invalid_json(self):
        response = self.client.post(
            self.NOVA_POSHTA_URL,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- Nova Poshta: real path (API key configured, network mocked) ---

    @override_settings(NOVA_POSHTA_API_KEY="test-key")
    def test_nova_poshta_real_path_maps_raw_fields(self):
        payload = {
            "success": True,
            "data": [
                {
                    "Description": "Відділення №1",
                    "Ref": "ref-1",
                    "ShortAddress": "м. Київ, вул. Тестова, 1",
                }
            ],
        }
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeUrlopenResponse(payload),
        ) as urlopen:
            response = self.client.post(
                self.NOVA_POSHTA_URL,
                {"city_name": "Київ"},
                format="json",
            )
        urlopen.assert_called_once()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json()["data"],
            [
                {
                    "name": "Відділення №1",
                    "ref": "ref-1",
                    "address": "м. Київ, вул. Тестова, 1",
                }
            ],
        )
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["apiKey"], "test-key")
        self.assertEqual(sent["methodProperties"]["CityName"], "Київ")

    @override_settings(NOVA_POSHTA_API_KEY="test-key")
    def test_nova_poshta_real_path_falls_back_to_generated_name(self):
        payload = {
            "success": True,
            "data": [
                {"Ref": "ref-short-address", "ShortAddress": "м. Львів, вул. Тестова, 2"},
                {"Ref": "ref-empty", "Description": "", "ShortAddress": ""},
            ],
        }
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeUrlopenResponse(payload),
        ):
            response = self.client.post(
                self.NOVA_POSHTA_URL,
                {"city_name": "Львів"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()["data"]
        self.assertEqual(data[0]["name"], "м. Львів, вул. Тестова, 2")
        self.assertEqual(data[1]["name"], "Відділення 2")
        self.assertEqual(data[1]["ref"], "ref-empty")
        self.assertEqual(data[1]["address"], "")

    @override_settings(NOVA_POSHTA_API_KEY="test-key")
    def test_nova_poshta_real_path_non_list_data_returns_empty_list(self):
        payload = {"success": True, "data": {"Description": "not a list"}}
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_FakeUrlopenResponse(payload),
        ):
            response = self.client.post(
                self.NOVA_POSHTA_URL,
                {"city_name": "Київ"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["data"], [])

    @override_settings(NOVA_POSHTA_API_KEY="test-key")
    def test_nova_poshta_real_path_returns_502_on_url_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=URLError("boom"),
        ):
            response = self.client.post(
                self.NOVA_POSHTA_URL,
                {"city_name": "Київ"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.json()["error"], "<urlopen error boom>")

    # --- Ukrposhta (mock only) ---

    def test_ukrposhta_warehouses_shape(self):
        response = self.client.post(
            self.UKRPOSHTA_URL,
            {"city_name": "Київ"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()["data"]
        self.assertEqual(len(data), 2)
        for item in data:
            self.assertEqual(set(item.keys()), {"name", "address"})
            self.assertTrue(item["name"])
            self.assertTrue(item["address"])

    def test_ukrposhta_requires_city_name(self):
        response = self.client.post(self.UKRPOSHTA_URL, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ukrposhta_rejects_invalid_json(self):
        response = self.client.post(
            self.UKRPOSHTA_URL,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class _FakeInventory:
    """In-memory stand-in for inventory-service's stock endpoints (same rules, one request = one transaction)."""

    def __init__(self, stock):
        self.stock = stock

    def post(self, url, payload, headers):
        path = url.split("/api/", 1)[1]
        if path == "stock/deduct_batch/":
            return self._batch(payload)
        row = self.stock[payload["product_id"]]
        q = payload["quantity"]
        if path == "stock/deduct/":
            if row["quantity"] - row["reserved"] < q:
                return _FakeResponse(400, {"error": "Insufficient available stock"})
            row["reserved"] -= min(row["reserved"], q)
            row["quantity"] -= q
        elif path == "stock/release/":
            if row["reserved"] < q:
                return _FakeResponse(400, {"error": "Cannot release more than reserved"})
            row["reserved"] -= q
        return _FakeResponse(200, row)

    def _batch(self, payload):
        rows = {i["product_id"]: dict(self.stock[i["product_id"]]) for i in payload["items"]}
        for item in payload["items"]:
            row, q = rows[item["product_id"]], item["quantity"]
            if payload.get("from_reserved"):
                if row["reserved"] < q or row["quantity"] < q:
                    return _FakeResponse(400, {"error": "Insufficient reserved stock"})
                row["reserved"] -= q
            elif row["quantity"] - row["reserved"] < q:
                return _FakeResponse(400, {"error": "Insufficient available stock"})
            row["quantity"] -= q
        self.stock.update(rows)
        return _FakeResponse(200, {"items": list(rows.values())})


class _FakeResponse:
    def __init__(self, status_code, data):
        self.status_code, self._data = status_code, data

    def json(self):
        return self._data


class DeductCompensationTest(APITestCase):
    """A failed write-off of the 2nd item must leave the 1st item's stock untouched (fix #2)."""

    def setUp(self):
        user = User.objects.create_superuser("admin", "admin@test.com", "password123")
        self.client.force_authenticate(user=user)

    def test_failed_delivery_deduct_restores_first_item(self):
        inventory = _FakeInventory({1: {"quantity": 5, "reserved": 1}, 2: {"quantity": 0, "reserved": 1}})
        order = _create_order(status=Order.PAID, warehouse_id=1)
        _create_order_item(order, product_id=1, quantity=1)
        _create_order_item(order, product_id=2, quantity=1)
        with mock.patch("orders.inventory_client._post", side_effect=inventory.post):
            response = self.client.patch(f"/api/orders/{order.pk}/status/", {"status": "delivering"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PAID)
        self.assertEqual(inventory.stock[1], {"quantity": 5, "reserved": 1})

    def test_delivering_ships_the_reserved_last_unit(self):
        inventory = _FakeInventory({1: {"quantity": 1, "reserved": 1}})
        order = _create_order(status=Order.PAID, warehouse_id=1)
        _create_order_item(order, product_id=1, quantity=1)
        with mock.patch("orders.inventory_client._post", side_effect=inventory.post):
            response = self.client.patch(f"/api/orders/{order.pk}/status/", {"status": "delivering"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(inventory.stock[1], {"quantity": 0, "reserved": 0})

    def test_failed_pos_deduct_restores_first_item(self):
        inventory = _FakeInventory({1: {"quantity": 5, "reserved": 2}, 2: {"quantity": 0, "reserved": 0}})
        with mock.patch("orders.inventory_client._post", side_effect=inventory.post):
            response = self.client.post("/api/orders/pos/", {
                "warehouse_id": 1, "customer_name": "POS", "customer_phone": "0671234567",
                "items": [{"product_id": 1, "quantity": 1, "price": "10.00"},
                          {"product_id": 2, "quantity": 1, "price": "10.00"}],
            }, format="json")
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(inventory.stock[1], {"quantity": 5, "reserved": 2})


class ReserveFailureCancellationTest(APITestCase):
    """A failed reservation cancels the order through update_order_status, so order.cancelled is published (fix #3)."""

    @mock.patch("orders.order_service.publish_event")
    @mock.patch("orders.views.release_stock", return_value=INVENTORY_OK)
    @mock.patch("orders.order_service.release_stock", return_value=INVENTORY_OK)
    @mock.patch("orders.order_service.reserve_stock", side_effect=[INVENTORY_OK, (None, "Insufficient available stock")])
    @mock.patch("orders.serializers.check_availability", return_value=(True, None))
    @mock.patch("orders.views.check_availability", return_value=(True, None))
    def test_failed_reserve_publishes_order_cancelled(self, _cv, _cs, _reserve, svc_release, view_release, publish):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/orders/", {
                "warehouse_id": 1, "customer_name": "John", "customer_email": "john@test.com",
                "items": [{"product_id": 1, "quantity": 1, "price": "10.00"},
                          {"product_id": 2, "quantity": 1, "price": "10.00"}],
            }, format="json")
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertIn("Stock reservation failed", order.notes)
        self.assertIn("order.cancelled", [c.args[0] for c in publish.call_args_list])
        # only the reserved item is released, once; the item that was never reserved is left alone
        released = [c.kwargs["product_id"] for c in svc_release.call_args_list + view_release.call_args_list]
        self.assertEqual(released, [1])


class EventOutboxTest(TestCase):
    """Events RabbitMQ rejects are kept in the outbox and re-sent once it is back (fix #4)."""

    def test_failed_publish_is_queued_then_relayed(self):
        from shared_eventbus import outbox, publisher
        from shared_eventbus.models import OutboxEvent

        with mock.patch.object(publisher, "get_channel", side_effect=ConnectionError("broker down")), \
                mock.patch.object(outbox, "_ensure_relay") as relay_started:
            ok, _ = publisher.publish_event("order.created", {"event_id": "e1", "total": Decimal("9.99")})
        self.assertFalse(ok)
        relay_started.assert_called_once()
        self.assertEqual(OutboxEvent.objects.get().payload, {"event_id": "e1", "total": "9.99"})

        sent = []
        with mock.patch.object(publisher, "send", side_effect=lambda key, data, own_connection=False: sent.append((key, data))):
            self.assertEqual(outbox.relay_pending(), 0)
        self.assertEqual(sent, [("order.created", {"event_id": "e1", "total": "9.99"})])
        self.assertFalse(OutboxEvent.objects.exists())

    def test_relay_keeps_events_while_broker_is_down(self):
        from shared_eventbus import outbox, publisher
        from shared_eventbus.models import OutboxEvent

        OutboxEvent.objects.create(routing_key="order.created", payload={"event_id": "e2"})
        with mock.patch.object(publisher, "send", side_effect=ConnectionError("still down")):
            self.assertEqual(outbox.relay_pending(), 1)
        self.assertEqual(OutboxEvent.objects.get().attempts, 1)


class CatalogPricingTest(APITestCase):
    """The client never decides what it pays (it used to send the price Stripe charged)."""

    def setUp(self):
        self.client.force_authenticate(user=_create_admin_user())

    # Stock is checked against the live inventory otherwise, and the test then depends on its data
    @mock.patch("orders.serializers.check_availability", return_value=(True, None))
    @mock.patch("orders.order_service.deduct_stock_batch", return_value=({"unit_costs": {"1": "120.50"}}, None))
    def test_pos_uses_catalog_price_and_stores_cost_on_deduct(self, _deduct, _available):
        response = self.client.post("/api/orders/pos/", {
            "warehouse_id": 1, "customer_name": "POS", "customer_phone": "380671234567",
            "items": [{"product_id": 1, "quantity": 2, "price": "0.01", "cost_price": "999.00"}],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = OrderItem.objects.get()
        self.assertEqual(item.price, CATALOG_PRICE)
        self.assertEqual(item.product_name, "Catalog product 1")
        self.assertEqual(item.cost_price, Decimal("120.50"))
        self.assertEqual(item.warranty_months, 24)
        self.assertEqual(Order.objects.get().total_amount, CATALOG_PRICE * 2)

    @mock.patch("orders.serializers.get_catalog_items", return_value={})
    def test_unknown_product_is_rejected(self, _):
        response = self.client.post("/api/orders/", {"items": [{"product_id": 404, "quantity": 1}]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Order.objects.exists())

    def test_catalog_outage_refuses_the_order(self):
        from .product_client import CatalogUnavailable
        with mock.patch("orders.serializers.get_catalog_items", side_effect=CatalogUnavailable("down")):
            response = self.client.post("/api/orders/", {"items": [{"product_id": 1, "quantity": 1}]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(Order.objects.exists())

    def test_negative_shipping_cost_is_rejected(self):
        response = self.client.post("/api/orders/", {
            "shipping_cost": "-500.00", "items": [{"product_id": 1, "quantity": 1}],
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ReturnsAPITest(APITestCase):
    """Returns (14 days) and warranty claims: eligibility, ownership and what each status change does."""

    URL = "/api/orders/returns/"

    def setUp(self):
        self.buyer = User.objects.create_user("gw_1", "buyer@test.com", "pw")
        self.admin = _create_admin_user()
        self.order = _create_order(status=Order.DELIVERED, warehouse_id=3, delivered_at=timezone.now() - timedelta(days=2))
        self.item = _create_order_item(self.order, quantity=2, price=Decimal("500.00"))
        self.client.force_authenticate(user=self.buyer)

    def _claim(self, kind="return", quantity=1, item=None):
        return self.client.post(self.URL, {
            "item_id": (item or self.item).pk, "kind": kind, "quantity": quantity, "reason": "Does not fit",
        }, format="json")

    def _set(self, ret_id, **body):
        self.client.force_authenticate(user=self.admin)
        return self.client.post(f"{self.URL}{ret_id}/status/", body, format="json")

    def test_buyer_files_and_sees_own_claim(self):
        response = self._claim()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "submitted")
        self.assertEqual([r["id"] for r in self.client.get(self.URL).data["results"]], [response.data["id"]])

    def test_order_detail_route_still_works(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(f"/api/orders/{self.order.pk}/").status_code, status.HTTP_200_OK)

    def test_someone_elses_order_is_not_found(self):
        self.client.force_authenticate(user=User.objects.create_user("gw_2", "x@test.com", "pw"))
        self.assertEqual(self._claim().status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(self.URL).data["results"], [])

    def test_not_yet_delivered_order_cannot_be_claimed(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.DELIVERING)
        self.assertEqual(self._claim().status_code, status.HTTP_400_BAD_REQUEST)

    def test_return_window_is_14_days_but_warranty_goes_on(self):
        Order.objects.filter(pk=self.order.pk).update(delivered_at=timezone.now() - timedelta(days=20))
        self.assertEqual(self._claim("return").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._claim("warranty").status_code, status.HTTP_201_CREATED)

    def test_warranty_uses_the_months_snapshotted_at_sale(self):
        OrderItem.objects.filter(pk=self.item.pk).update(warranty_months=1)
        Order.objects.filter(pk=self.order.pk).update(delivered_at=timezone.now() - timedelta(days=40))
        self.assertEqual(self._claim("warranty").status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_claim_more_units_than_bought(self):
        self.assertEqual(self._claim(quantity=2).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._claim(quantity=1).status_code, status.HTTP_400_BAD_REQUEST)

    def test_buyer_cannot_change_status(self):
        ret_id = self._claim().data["id"]
        response = self.client.post(f"{self.URL}{ret_id}/status/", {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch("orders.returns.stripe.Refund.create", return_value=mock.Mock(id="re_1"))
    @mock.patch("orders.returns.return_stock", return_value=INVENTORY_OK)
    def test_return_flow_restocks_and_refunds_through_stripe(self, restock, refund):
        Order.objects.filter(pk=self.order.pk).update(stripe_payment_intent_id="pi_1")
        ret_id = self._claim().data["id"]
        self.assertEqual(self._set(ret_id, status="approved").status_code, status.HTTP_200_OK)
        self.assertTrue(self._set(ret_id, status="received").data["restocked"])
        restock.assert_called_once()
        self.assertEqual(restock.call_args.kwargs["warehouse_id"], 3)
        data = self._set(ret_id, status="resolved", resolution="refund").data
        self.assertEqual(Decimal(data["refund_amount"]), Decimal("500.00"))
        refund.assert_called_once_with(payment_intent="pi_1", amount=50000, idempotency_key=f"rma-{ret_id}")

    @mock.patch("orders.returns.stripe.Refund.create")
    def test_failed_refund_leaves_the_claim_unresolved(self, refund):
        import stripe
        refund.side_effect = stripe.error.StripeError("card gone")
        Order.objects.filter(pk=self.order.pk).update(stripe_payment_intent_id="pi_1")
        ret = ReturnRequest.objects.create(order=self.order, item=self.item, quantity=1, kind="warranty",
                                           reason="x", status=ReturnRequest.RECEIVED)
        self.assertEqual(self._set(ret.pk, status="resolved", resolution="refund").status_code, 502)
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnRequest.RECEIVED)

    def test_warranty_unit_is_not_restocked_by_default_and_repair_frees_the_units(self):
        ret_id = self._claim("warranty", quantity=2).data["id"]
        self._set(ret_id, status="approved")
        self.assertFalse(self._set(ret_id, status="received").data["restocked"])
        self.assertEqual(self._set(ret_id, status="resolved", resolution="repair").status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.buyer)
        self.assertEqual(self._claim("warranty", quantity=2).status_code, status.HTTP_201_CREATED)

    def test_return_cannot_be_resolved_by_repair_and_rejection_needs_a_comment(self):
        ret_id = self._claim().data["id"]
        self.assertEqual(self._set(ret_id, status="rejected").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._set(ret_id, status="rejected", comment="Used item").status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.buyer)
        self.assertEqual(self._claim().status_code, status.HTTP_201_CREATED)  # rejected units are claimable again
        ret = ReturnRequest.objects.filter(status=ReturnRequest.SUBMITTED).get()
        ReturnRequest.objects.filter(pk=ret.pk).update(status=ReturnRequest.RECEIVED)
        self.assertEqual(self._set(ret.pk, status="resolved", resolution="repair").status_code, status.HTTP_400_BAD_REQUEST)


class PurchasedCheckTest(APITestCase):
    def test_only_handed_over_orders_of_this_user_count(self):
        buyer = User.objects.create_user("gw_1", "b@test.com", "pw")
        _create_order_item(_create_order(status=Order.DELIVERED), product_id=5)
        _create_order_item(_create_order(order_number="ORD-2", status=Order.PAID), product_id=6)
        _create_order_item(_create_order(order_number="ORD-3", status=Order.COMPLETED, created_by="gw_2"), product_id=7)
        self.client.force_authenticate(user=buyer)
        got = {pid: self.client.get(f"/api/orders/purchased/?product_id={pid}").data["purchased"] for pid in (5, 6, 7)}
        self.assertEqual(got, {5: True, 6: False, 7: False})
        self.assertEqual(self.client.get("/api/orders/purchased/?product_id=x").status_code, status.HTTP_400_BAD_REQUEST)



@_stub_inventory
class PromoCodeTest(APITestCase):
    """Codes are checked and applied on the server, counted under a lock and given back on cancel."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # promo-check throttle
        self.code = PromoCode.objects.create(code="SPRING10", kind=PromoCode.PERCENT, value=Decimal("10"))

    def _order(self, code="spring10", email="a@test.com", qty=1):
        # Catalog stub: 199.99 per unit
        return self.client.post("/api/orders/", {
            "customer_email": email, "promo_code": code, "warehouse_id": 1,
            "items": [{"product_id": 1, "quantity": qty}],
        }, format="json")

    def test_percent_code_is_applied_and_counted(self):
        response = self._order()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        order = Order.objects.get()
        self.assertEqual((order.discount_amount, order.total_amount), (Decimal("20.00"), Decimal("179.99")))
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 1)

    def test_fixed_code_never_makes_the_order_negative(self):
        PromoCode.objects.create(code="BIG", kind=PromoCode.FIXED, value=Decimal("5000"))
        self._order("BIG")
        self.assertEqual(Order.objects.get().total_amount, Decimal("0.00"))

    def test_unknown_expired_used_up_and_minimum_are_rejected(self):
        PromoCode.objects.create(code="OLD", kind=PromoCode.FIXED, value=1, ends_at=timezone.now() - timedelta(days=1))
        PromoCode.objects.create(code="GONE", kind=PromoCode.FIXED, value=1, max_uses=1, used_count=1)
        PromoCode.objects.create(code="MIN", kind=PromoCode.FIXED, value=1, min_order_amount=Decimal("1000"))
        PromoCode.objects.create(code="OFF", kind=PromoCode.FIXED, value=1, is_active=False)
        for code in ("NOPE", "OLD", "GONE", "MIN", "OFF"):
            self.assertEqual(self._order(code).status_code, status.HTTP_400_BAD_REQUEST, code)
        self.assertFalse(Order.objects.exists())

    def test_once_per_customer_and_cancel_gives_the_use_back(self):
        self.code.once_per_customer = True
        self.code.max_uses = 1
        self.code.save()
        self.assertEqual(self._order().status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._order(email="A@test.com").status_code, status.HTTP_400_BAD_REQUEST)
        svc.update_order_status(Order.objects.get(), Order.CANCELLED, release_reserved=False)
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 0)
        self.assertEqual(self._order().status_code, status.HTTP_201_CREATED)

    def test_check_endpoint_previews_the_discount(self):
        data = self.client.get("/api/orders/promo-check/?code=spring10&subtotal=1000").data
        self.assertEqual((data["discount"], data["total"]), ("100.00", "900.00"))
        self.assertEqual(self.client.get("/api/orders/promo-check/?code=nope&subtotal=10").status_code, 400)

    def test_check_endpoint_is_throttled(self):
        codes = [self.client.get("/api/orders/promo-check/?code=X&subtotal=1").status_code for _ in range(12)]
        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, codes)

    def test_only_admins_manage_codes(self):
        self.assertIn(self.client.get("/api/orders/promo-codes/").status_code, (401, 403))
        self.client.force_authenticate(user=_create_admin_user())
        response = self.client.post("/api/orders/promo-codes/", {"code": " summer-5 ", "kind": "percent", "value": "105"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post("/api/orders/promo-codes/", {"code": " summer-5 ", "kind": "percent", "value": "5"}, format="json")
        self.assertEqual(response.data["code"], "SUMMER-5")

    def test_reports_net_out_the_discount_and_refunds_are_proportional(self):
        from .returns import refund_amount
        self._order(qty=2)  # 399.98 - 40.00
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(status=Order.PAID)
        self.client.force_authenticate(user=_create_admin_user())
        data = self.client.get("/api/reports/sales/").data
        self.assertEqual((data["total_revenue"], data["total_discount"]), (359.98, 40.0))
        self.assertEqual(refund_amount(order.items.get(), 1), Decimal("179.99"))


@_stub_inventory
class MetricsEndpointTest(APITestCase):
    def test_metrics_expose_http_and_business_counters(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/api/orders/", {"warehouse_id": 1, "items": [{"product_id": 1, "quantity": 1}]}, format="json")
        body = self.client.get("/metrics").content.decode()
        self.assertIn('techhub_orders_created_total{channel="online"}', body)
        # Router URLs are regexes, so that is the route label
        self.assertIn('http_requests_total{method="POST",route="api/orders/$",status="201"}', body)


@_stub_inventory
class PayPermissionTest(APITestCase):
    """Only the buyer can start a Stripe checkout for an order (it used to be admin-only, so nobody could pay)."""

    def _create(self):
        with mock.patch("orders.views.check_availability", return_value=(True, None)):
            return self.client.post("/api/orders/", {
                "customer_email": "g@test.com", "warehouse_id": 1, "items": [{"product_id": 1, "quantity": 1}],
            }, format="json").data

    @mock.patch("orders.views.stripe.checkout.Session.create", return_value=mock.Mock(id="cs_1", url="https://stripe.test/cs_1"))
    def test_guest_pays_with_the_token_from_create(self, _session):
        order = self._create()
        response = self.client.post(f"/api/orders/{order['id']}/pay/", {"token": order["pay_token"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["checkout_url"], "https://stripe.test/cs_1")

    def test_wrong_or_missing_token_is_not_found(self):
        order = self._create()
        other = self._create()
        for body in ({}, {"token": "forged"}, {"token": other["pay_token"]}):
            self.assertEqual(self.client.post(f"/api/orders/{order['id']}/pay/", body, format="json").status_code, 404)

    @mock.patch("orders.views.stripe.checkout.Session.create", return_value=mock.Mock(id="cs_2", url="https://stripe.test/cs_2"))
    def test_signed_in_owner_pays_without_a_token_but_others_cannot(self, _session):
        owner = User.objects.create_user("gw_9", "o@test.com", "pw")
        self.client.force_authenticate(user=owner)
        order = self._create()
        self.assertEqual(self.client.post(f"/api/orders/{order['id']}/pay/", {}, format="json").status_code, 200)
        self.client.force_authenticate(user=User.objects.create_user("gw_10", "x@test.com", "pw"))
        self.assertEqual(self.client.post(f"/api/orders/{order['id']}/pay/", {}, format="json").status_code, 404)



class CompleteOldPosSalesMigrationTest(TestCase):
    def test_marks_only_unpaid_offline_orders_completed(self):
        from importlib import import_module

        from django.apps import apps

        old_pos = _create_order(channel=Order.OFFLINE)
        online = _create_order(order_number="ORD-000002")
        cancelled_pos = _create_order(order_number="ORD-000003", channel=Order.OFFLINE, status=Order.CANCELLED)

        import_module("orders.migrations.0009_complete_old_pos_sales").complete_old_pos_sales(apps, None)

        old_pos.refresh_from_db()
        self.assertEqual((old_pos.status, old_pos.payment_status), (Order.COMPLETED, Order.PAYMENT_PAID))
        self.assertEqual(old_pos.paid_at, old_pos.created_at)
        online.refresh_from_db()
        cancelled_pos.refresh_from_db()
        self.assertEqual(online.status, Order.UNPAID)
        self.assertEqual(cancelled_pos.status, Order.CANCELLED)


@mock.patch("orders.order_service.return_stock", return_value=INVENTORY_OK)
@mock.patch("orders.order_service.release_stock", return_value=INVENTORY_OK)
class CancelSideEffectsTest(TestCase):
    def _order(self, status, **fields):
        order = Order.objects.create(
            order_number=f"ORD-{Order.objects.count() + 1}", status=status, warehouse_id=1,
            total_amount=Decimal("200.00"), **fields,
        )
        OrderItem.objects.create(order=order, product_id=7, quantity=2, price=Decimal("100.00"))
        return order

    def test_cancel_after_shipping_returns_goods_instead_of_releasing_others_reserve(self, release, ret):
        """The shipped reserve is already written off; releasing it would free another order's units (fix #27)."""
        for shipped in (Order.DELIVERING, Order.DELIVERED):
            release.reset_mock(), ret.reset_mock()
            _, error = svc.update_order_status(self._order(shipped), Order.CANCELLED)
            self.assertIsNone(error)
            release.assert_not_called()
            self.assertEqual(ret.call_args.kwargs["quantity"], 2)

    def test_cancel_before_shipping_releases_reserve(self, release, ret):
        for waiting in (Order.UNPAID, Order.PAID):
            release.reset_mock()
            svc.update_order_status(self._order(waiting), Order.CANCELLED)
            release.assert_called_once()
        ret.assert_not_called()

    @mock.patch("orders.order_service.stripe.Refund.create", return_value=mock.Mock(id="re_1"))
    def test_cancel_paid_order_refunds_it(self, refund, release, ret):
        """Cancelling a paid order gives the money back (fix #28)."""
        order = self._order(Order.PAID, payment_status=Order.PAYMENT_PAID, stripe_payment_intent_id="pi_1")
        _, error = svc.update_order_status(order, Order.CANCELLED)
        self.assertIsNone(error)
        refund.assert_called_once_with(payment_intent="pi_1", idempotency_key=f"cancel-{order.id}")
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (Order.CANCELLED, Order.PAYMENT_REFUNDED))

    @mock.patch("orders.order_service.stripe.Refund.create")
    def test_failed_refund_keeps_order_paid(self, refund, release, ret):
        import stripe
        refund.side_effect = stripe.error.StripeError("card gone")
        order = self._order(Order.PAID, payment_status=Order.PAYMENT_PAID, stripe_payment_intent_id="pi_1")
        _, error = svc.update_order_status(order, Order.CANCELLED)
        self.assertEqual(error["error"], "Refund failed")
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (Order.PAID, Order.PAYMENT_PAID))
        release.assert_not_called()


class InternalTokenHeaderTest(TestCase):
    """Calls to inventory carry the service token, so a guest or the webhook can reserve (fix #33)."""

    @override_settings(INTERNAL_SERVICE_TOKEN="s3cret")
    def test_guest_reserve_sends_the_service_token(self):
        from . import inventory_client
        with mock.patch.object(inventory_client, "_post", return_value=mock.Mock(status_code=200, json=lambda: {})) as post:
            inventory_client.reserve_stock(5, 2, 1, "order", "1", "reserve-1-5", request=None)
        self.assertEqual(post.call_args.args[2]["X-Internal-Token"], "s3cret")


@mock.patch("orders.views.check_availability", new=mock.Mock(return_value=(True, None)))
@mock.patch("orders.order_service.release_stock", return_value=INVENTORY_OK)
@mock.patch("orders.order_service.deduct_stock_batch", return_value=INVENTORY_OK)
@mock.patch("orders.order_service.reserve_stock", return_value=INVENTORY_OK)
class DeliveryOrderStockTest(APITestCase):
    """A delivery order ships from a warehouse the server picks, so it moves stock like pickup (fix #32)."""

    def _create(self, **fields):
        return self.client.post("/api/orders/", {
            "delivery_method": "nova_poshta", "shipping_city": "Kyiv", "shipping_address": "NP 1",
            "customer_name": "Guest", "customer_phone": "380671234567", "customer_email": "g@test.com",
            "items": [{"product_id": 7, "quantity": 2}], **fields,
        }, format="json")

    @mock.patch("orders.serializers.pick_fulfilment_warehouse", return_value=(4, None))
    def test_delivery_order_reserves_ships_and_cancels_through_its_warehouse(self, _pick, reserve, deduct, release):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        order = Order.objects.get()
        self.assertEqual(order.warehouse_id, 4)
        self.assertEqual((reserve.call_args.kwargs["warehouse_id"], reserve.call_args.kwargs["quantity"]), (4, 2))

        svc.update_order_status(order, Order.PAID)
        svc.update_order_status(order, Order.DELIVERING)
        self.assertEqual(deduct.call_args.kwargs["warehouse_id"], 4)

        other = Order.objects.get(pk=self._create().data["id"])
        svc.update_order_status(other, Order.CANCELLED)
        self.assertEqual(release.call_args.kwargs["warehouse_id"], 4)

    @mock.patch("orders.serializers.pick_fulfilment_warehouse", return_value=(None, None))
    def test_shortage_rejects_before_the_order_exists(self, _pick, reserve, *_):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Order.objects.exists())
        reserve.assert_not_called()

    @mock.patch("orders.serializers.pick_fulfilment_warehouse", return_value=(None, "HTTP 503"))
    def test_inventory_outage_refuses_the_order(self, *_):
        self.assertEqual(self._create().status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(Order.objects.exists())

    def test_pickup_without_a_showroom_is_rejected(self, *_):
        self.assertEqual(self._create(delivery_method="pickup").status_code, status.HTTP_400_BAD_REQUEST)


class PickFulfilmentWarehouseTest(TestCase):
    WAREHOUSES = [
        {"id": 2, "type": "showroom", "is_active": True},
        {"id": 3, "type": "warehouse", "is_active": False},
        {"id": 4, "type": "warehouse", "is_active": True},
        {"id": 1, "type": "warehouse", "is_active": True},
    ]
    STOCK = {
        7: [{"warehouse": w, "product_id": 7, "quantity": q, "reserved": r} for w, q, r in ((1, 5, 4), (2, 9, 0), (3, 9, 0), (4, 3, 0))],
        8: [{"warehouse": w, "product_id": 8, "quantity": 9, "reserved": 0} for w in (1, 4)],
    }

    def _get(self, url, headers, params=None):
        body = {"results": self.WAREHOUSES} if url.endswith("warehouses/") else self.STOCK[params["product_id"]]
        return mock.Mock(status_code=200, json=lambda: body)

    def test_first_active_warehouse_holding_every_item(self):
        from . import inventory_client
        with mock.patch.object(inventory_client, "_get", side_effect=self._get):
            # 1 has only 1 free unit of product 7; showroom 2 and inactive 3 never ship
            self.assertEqual(inventory_client.pick_fulfilment_warehouse(
                [{"product_id": 7, "quantity": 2}, {"product_id": 8, "quantity": 1}]), (4, None))
            self.assertEqual(inventory_client.pick_fulfilment_warehouse([{"product_id": 7, "quantity": 4}]), (None, None))

    @override_settings(DEFAULT_FULFILMENT_WAREHOUSE_ID="3")
    def test_configured_warehouse_wins(self):
        from . import inventory_client
        self.assertEqual(inventory_client.pick_fulfilment_warehouse([{"product_id": 7, "quantity": 1}]), (3, None))


@mock.patch("orders.order_service.stripe.Refund.create", return_value=mock.Mock(id="re_1"))
@mock.patch("orders.management.commands.cancel_stuck_orders.release_stock", return_value=INVENTORY_OK)
class CancelStuckOrdersTest(TestCase):
    """Only a paid order that cannot hold its reserve is compensated, not every one waiting to ship (fix #36)."""

    def _paid(self, warehouse_id=1, products=(7,)):
        order = Order.objects.create(
            order_number=f"ORD-S{Order.objects.count() + 1}", status=Order.PAID, warehouse_id=warehouse_id,
            payment_status=Order.PAYMENT_PAID, stripe_payment_intent_id="pi_1", total_amount=Decimal("100.00"),
        )
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now() - timedelta(hours=2))
        for pid in products:
            OrderItem.objects.create(order=order, product_id=pid, quantity=1, price=Decimal("50.00"))
        return order

    def _run(self, *args):
        from django.core.management import call_command
        call_command("cancel_stuck_orders", *args, stdout=mock.Mock())

    @mock.patch("orders.order_service.reserve_stock", return_value=INVENTORY_OK)  # 409 replay: already reserved
    def test_paid_order_holding_its_reserve_stays(self, reserve, release, refund):
        order = self._paid()
        self._run()
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (Order.PAID, Order.PAYMENT_PAID))
        self.assertEqual(reserve.call_args.kwargs["idempotency_key"], f"reserve-{order.id}-7")
        release.assert_not_called()
        refund.assert_not_called()

    @mock.patch("orders.order_service.reserve_stock", side_effect=[INVENTORY_OK, (None, "Insufficient available stock")])
    def test_paid_order_without_a_reserve_is_cancelled_and_refunded(self, reserve, release, refund):
        order = self._paid(products=(7, 8))
        self._run()
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (Order.CANCELLED, Order.PAYMENT_REFUNDED))
        refund.assert_called_once()
        # Only the line that does hold a reserve gives it back
        self.assertEqual([c.kwargs["product_id"] for c in release.call_args_list], [7])

    @mock.patch("orders.order_service.reserve_stock", return_value=INVENTORY_OK)
    def test_orders_without_a_warehouse_and_dry_runs_touch_nothing(self, reserve, release, refund):
        no_warehouse, stuck = self._paid(warehouse_id=None), self._paid()
        self._run("--dry-run")
        self._run()  # no_warehouse: nothing was ever reserved, so nothing to compensate
        reserve.assert_called_once()
        self.assertEqual(reserve.call_args.kwargs["reference_id"], str(stuck.id))
        no_warehouse.refresh_from_db()
        self.assertEqual(no_warehouse.status, Order.PAID)
