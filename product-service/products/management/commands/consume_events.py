import logging
import threading

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

from products.eventbus import start_consumer

logger = logging.getLogger(__name__)

RECONCILE_EVERY_SECONDS = 3600


def reconcile_forever(stop):
    """Hourly Product.stock check against inventory, so a lost event does not drift for good."""
    while not stop.wait(RECONCILE_EVERY_SECONDS):
        try:
            call_command("reconcile_stock")
        except Exception:
            # The consumer keeps running; the next hour tries again
            logger.exception("Stock reconcile failed")
        finally:
            connection.close()


class Command(BaseCommand):
    help = "Start RabbitMQ consumer for product events"

    def handle(self, *args, **options):
        self.stdout.write("Starting product event consumer...")
        threading.Thread(target=reconcile_forever, args=(threading.Event(),), daemon=True).start()
        start_consumer()
