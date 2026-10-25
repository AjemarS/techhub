"""
Django middleware that captures HTTP request/response audit events.
Sends async fire-and-forget POST to audit-service.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

AUDIT_SERVICE_URL = getattr(settings, "AUDIT_SERVICE_URL", "http://audit-service:8005")

# A few long-lived threads with keep-alive sessions, not a new thread and connection per request
POOL_SIZE = 2
# Events waiting to be sent; past this (audit-service down or slow) new ones are dropped
MAX_PENDING = 1000

_pool = None
_pool_lock = threading.Lock()
_pending = threading.BoundedSemaphore(MAX_PENDING)
_local = threading.local()


def _executor():
    # Created on first use, i.e. inside the gunicorn worker after the fork
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="audit")
    return _pool


def _post(payload):
    try:
        session = getattr(_local, "session", None)
        if session is None:
            session = _local.session = requests.Session()
        session.post(f"{AUDIT_SERVICE_URL}/api/audit/log", json=payload, timeout=1)
    except requests.RequestException:
        pass
    finally:
        _pending.release()


class AuditMiddleware:
    """Logs HTTP request metadata to the central audit-service."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.time()
        response = self.get_response(request)
        duration_ms = int((time.time() - start) * 1000)

        if _pending.acquire(blocking=False):
            _executor().submit(_post, {
                "request_id": request.META.get("HTTP_X_REQUEST_ID", ""),
                "event_type": "http_request",
                "service": request.META.get("HTTP_HOST", "unknown"),
                "method": request.method,
                "path": request.path,
                "user_id": request.META.get("HTTP_X_GATEWAY_USER_ID", ""),
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            })
        else:
            logger.warning("Audit backlog full, event dropped | path=%s", request.path)

        return response
