"""
Cached catalog responses for anonymous reads. Every cached entry is keyed by the catalog version,
and any change to products, categories, stock or ratings sets a new version, so a stale entry is
never read again: it just expires.
"""
import hashlib
import time
from functools import wraps

from django.core.cache import cache
from rest_framework.response import Response

VERSION_KEY = "catalog:version"
# Short enough for the live USD rate in English prices; data changes are covered by the version
TTL = 60


def version():
    v = cache.get(VERSION_KEY)
    if v is None:
        v = time.time_ns()
        cache.add(VERSION_KEY, v, None)
    return v


def bump():
    """Call after any change the catalog shows; shared by web workers and the consumer process."""
    cache.set(VERSION_KEY, time.time_ns(), None)


def cached_response(view):
    """Cache a read-only DRF action's 200 body by catalog version, absolute URL and locale."""

    @wraps(view)
    def wrapper(self, request, *args, **kwargs):
        from .serializers import _get_locale  # serializers import models, which import this module

        # Absolute URL: pagination links carry the host and scheme
        raw = f"{request.build_absolute_uri()}|{_get_locale(request)}"
        key = f"catalog:{version()}:{hashlib.sha1(raw.encode()).hexdigest()}"
        data = cache.get(key)
        if data is not None:
            return Response(data)
        response = view(self, request, *args, **kwargs)
        if response.status_code == 200:
            cache.set(key, response.data, TTL)
        return response

    return wrapper
