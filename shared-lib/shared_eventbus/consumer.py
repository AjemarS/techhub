import json
import logging

from .connection import get_connection

logger = logging.getLogger(__name__)


def dedup_check(event_id, ProcessedEvent):
    return ProcessedEvent.objects.filter(event_id=event_id).exists()


def dedup_claim(event_id, ProcessedEvent):
    from django.db import transaction

    with transaction.atomic():
        ProcessedEvent.objects.create(event_id=event_id)


def start_consumer(queue_map, consumer_name):
    """queue_map: queue name -> handler(event, event_id)."""
    try:
        conn = get_connection()
        channel = conn.channel()
        for queue_name in queue_map:
            channel.queue_declare(queue=queue_name, durable=True)

        def make_callback(handler):
            def callback(ch, method, properties, body):
                try:
                    event = json.loads(body.decode())
                    event_id = properties.message_id or event.get("event_id", "")
                    logger.info("Event received | key=%s event_id=%s", method.routing_key, event_id)
                    handler(event, event_id)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as e:
                    logger.error("Event processing failed: %s", e)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return callback

        channel.basic_qos(prefetch_count=1)
        for queue_name, handler in queue_map.items():
            channel.basic_consume(queue=queue_name, on_message_callback=make_callback(handler))

        logger.info("%s consumer started. Waiting for events...", consumer_name)
        channel.start_consuming()
    except Exception as e:
        logger.error("Consumer error: %s", e)
