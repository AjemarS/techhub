import logging
import os
import sys

from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class ResilientRedisCache(RedisCache):
    """
    Redis cache that treats an outage as a miss instead of a 500: cached pages are rebuilt and
    throttling lets requests through until Redis is back, but the API stays up.
    """

    def _safe(self, op, fallback, *args, **kwargs):
        try:
            return getattr(super(), op)(*args, **kwargs)
        except RedisError as e:
            logger.warning("Redis unavailable, cache %s skipped: %s", op, e)
            return fallback

    def get(self, key, default=None, version=None):
        return self._safe("get", default, key, default, version)

    def set(self, key, value, timeout=None, version=None):
        # Django's default timeout is a sentinel, so it is only passed on when given
        args = (key, value) if timeout is None else (key, value, timeout)
        return self._safe("set", None, *args, version=version)

    def add(self, key, value, timeout=None, version=None):
        args = (key, value) if timeout is None else (key, value, timeout)
        return self._safe("add", False, *args, version=version)

    def delete(self, key, version=None):
        return self._safe("delete", False, key, version)


def redis_caches(db):
    """
    Django CACHES for one service: Redis database `db`, shared by every gunicorn worker and by the
    service's consumer process. Without REDIS_URL, and under `manage.py test`, each process keeps
    its own in-memory cache, so tests never share counters or cached responses.
    """
    url = os.environ.get("REDIS_URL", "")
    if not url or sys.argv[1:2] == ["test"]:
        return {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    return {
        "default": {
            "BACKEND": "shared_auth.cache.ResilientRedisCache",
            "LOCATION": f"{url.rstrip('/')}/{db}",
            # A stalled Redis must not stall requests: give up fast and treat it as a miss
            "OPTIONS": {"socket_connect_timeout": 0.5, "socket_timeout": 0.5},
        }
    }
