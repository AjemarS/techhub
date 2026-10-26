import json
import logging
import time

from pika.exceptions import AMQPConnectionError

from .connection import get_connection

logger = logging.getLogger(__name__)


def dedup_check(event_id, ProcessedEvent):
    return ProcessedEvent.objects.filter(event_id=event_id).exists()


def dedup_claim(event_id, ProcessedEvent):
    from django.db import transaction

    with transaction.atomic():
        ProcessedEvent.objects.create(event_id=event_id)


RECONNECT_MAX_DELAY = 30  # seconds between attempts once the broker has been gone a while


def start_consumer(queue_map, consumer_name, setup=None):
    """
    queue_map: queue name -> handler(event, event_id). setup(channel), if given, runs on every
    (re)connect, e.g. to declare bindings. When the broker goes away (or is not up yet) this keeps
    reconnecting with backoff; it used to return and let the process exit until Docker restarted it.
    Returns when consuming stops cleanly or on an unexpected error.
    """
    delay = 1
    while True:
        started = time.monotonic()
        try:
            _consume(queue_map, consumer_name, setup)
            return
        # OSError: with the broker's container stopped, even its name does not resolve (socket.gaierror)
        except (AMQPConnectionError, OSError) as e:
            if time.monotonic() - started > RECONNECT_MAX_DELAY:
                delay = 1  # it had been connected for a while: try again straight away
            logger.warning("%s consumer lost RabbitMQ (%r); reconnecting in %ds", consumer_name, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)
        except Exception as e:
            logger.error("Consumer error: %s", e)
            return


def _consume(queue_map, consumer_name, setup):
    conn = get_connection()
    channel = conn.channel()
    if setup:
        setup(channel)
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
