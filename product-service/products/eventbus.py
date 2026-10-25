import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone
from shared_eventbus.connection import ensure_exchange, get_connection
from shared_eventbus.consumer import dedup_check, dedup_claim
from shared_eventbus.consumer import start_consumer as shared_start_consumer
from shared_eventbus.publisher import publish_event

from . import catalog_cache
from .models import ProcessedEvent, Product

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "techhub.events"


def with_dedup(handler):
    """Wrap handler with ProcessedEvent dedup. Handles concurrent-consumer race."""

    def wrapped(event, event_id):
        if event_id and dedup_check(event_id, ProcessedEvent):
            logger.info("Duplicate event skipped | event_id=%s", event_id)
            return
        if event_id:
            try:
                dedup_claim(event_id, ProcessedEvent)
            except IntegrityError:
                logger.info("Duplicate event skipped (race) | event_id=%s", event_id)
                return
        handler(event, event_id)

    return wrapped


def _adjust_stock(product_id, delta):
    """Atomically adjust stock, sync in_stock, publish alert if zeroed."""
    with transaction.atomic():
        Product.objects.filter(pk=product_id).update(stock=Greatest(F("stock") + delta, Value(0)))
        Product.objects.filter(pk=product_id, stock__gt=0).update(in_stock=True)
        zeroed = Product.objects.filter(pk=product_id, stock=0).update(in_stock=False)
    catalog_cache.bump()
    if zeroed:
        publish_event(
            "inventory.stock.critical",
            {
                "product_id": product_id,
                "stock": 0,
            },
        )
    return zeroed


def _handle_stock_changed(event, event_id=None):
    product_id = event.get("product_id")
    change = event.get("change", 0)
    if not product_id:
        logger.warning("inventory.stock.changed missing product_id")
        return
    try:
        _adjust_stock(product_id, change)
        logger.info("Stock synced | product=%s delta=%+d", product_id, change)
    except Exception as e:
        logger.error("Stock sync failed | product=%s error=%s", product_id, e)


# A goods receipt also publishes inventory.stock.changed, which is the one source of Product.stock;
# inventory.goods_received stays for the audit log only
QUEUE_MAP = {
    "product.stock.sync": with_dedup(_handle_stock_changed),
}


def setup_bindings(channel):
    """Declare exchange and bind queues to routing keys."""
    ensure_exchange(channel)
    # Old queue bound to inventory.goods_received counted every receipt a second time
    channel.queue_delete(queue="product.catalog.update")
    channel.queue_declare(queue="product.stock.sync", durable=True)
    channel.queue_bind(
        queue="product.stock.sync", exchange=EXCHANGE_NAME, routing_key="inventory.stock.changed"
    )


def start_consumer():
    conn = get_connection()
    setup_bindings(conn.channel())
    # Purge ProcessedEvent records older than 7 days on startup
    cutoff = timezone.now() - timedelta(days=7)
    purged, _ = ProcessedEvent.objects.filter(processed_at__lt=cutoff).delete()
    if purged:
        logger.info("Purged %d stale ProcessedEvent records", purged)
    shared_start_consumer(QUEUE_MAP, "Product")
