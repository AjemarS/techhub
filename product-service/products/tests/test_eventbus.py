import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase

from ..eventbus import _handle_stock_changed, start_consumer
from ..models import Category, Product


class EventbusHandlerTest(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Appliances")
        self.product = Product.objects.create(
            name="Test Fridge",
            category=self.category,
            price=Decimal("999.99"),
            original_price=Decimal("1299.99"),
            stock=50,
            in_stock=True,
        )

    def test_handle_stock_changed_increments_stock(self):
        _handle_stock_changed({
            "product_id": self.product.pk,
            "change": 10,
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 60)
        self.assertTrue(self.product.in_stock)

    def test_handle_stock_changed_decrements_stock(self):
        _handle_stock_changed({
            "product_id": self.product.pk,
            "change": -20,
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 30)
        self.assertTrue(self.product.in_stock)

    def test_handle_stock_changed_sets_in_stock_false(self):
        _handle_stock_changed({
            "product_id": self.product.pk,
            "change": -50,
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertFalse(self.product.in_stock)

    def test_handle_stock_changed_missing_product_id(self):
        _handle_stock_changed({"change": 10})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 50)


class FakeChannel:
    """In-memory broker: records bindings and consumers, delivers by routing key."""

    def __init__(self):
        self.bindings, self.consumers, self.nacked = {}, {}, 0

    def queue_bind(self, queue, exchange, routing_key):
        self.bindings.setdefault(routing_key, []).append(queue)

    def basic_consume(self, queue, on_message_callback):
        self.consumers[queue] = on_message_callback

    def basic_nack(self, delivery_tag, requeue):
        self.nacked += 1

    def __getattr__(self, name):
        return lambda *a, **kw: None

    def publish(self, routing_key, event):
        for queue in self.bindings.get(routing_key, []):
            self.consumers[queue](
                self, SimpleNamespace(routing_key=routing_key, delivery_tag=1),
                SimpleNamespace(message_id=None), json.dumps(event).encode(),
            )


class GoodsReceiptRoutingTest(TestCase):
    """A GRN publishes stock.changed and goods_received; the catalog counts it once (fix #25)."""

    def test_receipt_of_10_adds_10(self):
        product = Product.objects.create(
            name="Kettle", category=Category.objects.create(name="Kitchen"),
            price=Decimal("10"), original_price=Decimal("10"), stock=50,
        )
        channel = FakeChannel()
        conn = mock.Mock(channel=lambda: channel)
        with mock.patch("shared_eventbus.consumer.get_connection", return_value=conn):
            start_consumer()

        channel.publish(
            "inventory.stock.changed",
            {"event_id": str(uuid4()), "product_id": product.pk, "change": 10},
        )
        channel.publish(
            "inventory.goods_received",
            {"event_id": str(uuid4()), "product_id": product.pk, "quantity": 10},
        )

        product.refresh_from_db()
        self.assertEqual(product.stock, 60)
        self.assertEqual(channel.nacked, 0)


class ReconcileStockTest(TestCase):
    """A lost event no longer leaves Product.stock wrong for good (fix #30)."""

    def test_distorted_stock_is_restored_from_inventory(self):
        category = Category.objects.create(name="Kitchen")
        def make(name, stock):
            return Product.objects.create(
                name=name, category=category, price=1, original_price=1, stock=stock,
            )

        drifted, right, untracked = make("Kettle", 3), make("Toaster", 5), make("Mixer", 9)
        rows = [
            {"product_id": drifted.pk, "quantity": 7}, {"product_id": drifted.pk, "quantity": 4},
            {"product_id": right.pk, "quantity": 5},
        ]
        with mock.patch("products.management.commands.reconcile_stock.requests.get") as get:
            get.return_value.json.return_value = rows
            out = StringIO()
            call_command("reconcile_stock", stdout=out)

        for product, stock in ((drifted, 11), (right, 5), (untracked, 9)):
            product.refresh_from_db()
            self.assertEqual(product.stock, stock)
        self.assertIn("stock_reconcile_fixed 1", out.getvalue())


class ConsumerReconnectTest(TestCase):
    """When RabbitMQ is gone or not up yet the consumer reconnects instead of exiting."""

    def _run(self, connections):
        from pika.exceptions import AMQPConnectionError, StreamLostError

        channel = FakeChannel()
        lost = mock.Mock()
        lost.channel.return_value.start_consuming.side_effect = StreamLostError("broker gone")
        ok = mock.Mock(channel=lambda: channel)
        import socket

        by_name = {
            "down": AMQPConnectionError("refused"),
            "dns": socket.gaierror(-2, "Name or service not known"),
            "lost": lost,
            "ok": ok,
        }
        side_effect = [by_name[c] for c in connections]
        get = mock.patch("shared_eventbus.consumer.get_connection", side_effect=side_effect)
        sleep = mock.patch("shared_eventbus.consumer.time.sleep")
        with get as get_mock, sleep as sleep_mock:
            start_consumer()
        return get_mock, sleep_mock, channel

    def test_waits_for_a_broker_that_is_not_up_yet(self):
        # A stopped broker container: first its name does not resolve, then it refuses connections
        get, sleep, channel = self._run(["dns", "down", "ok"])
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])  # backoff
        self.assertIn("product.stock.sync", channel.consumers)
        self.assertEqual(channel.bindings["inventory.stock.changed"], ["product.stock.sync"])

    def test_reconnects_and_rebinds_after_losing_the_broker(self):
        get, _, channel = self._run(["lost", "ok"])
        self.assertEqual(get.call_count, 2)
        # Bindings are declared again on the new connection, in case the broker lost them
        self.assertEqual(channel.bindings["inventory.stock.changed"], ["product.stock.sync"])
