import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission

from shared_auth.permissions import IsAdminOrWarehouseWorker  # noqa: F401


class IsAuthenticatedOrInternalService(BasePermission):
    """A signed-in user, or another service carrying the shared X-Internal-Token (guest orders, webhooks)."""

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        token = settings.INTERNAL_SERVICE_TOKEN
        sent = request.headers.get("X-Internal-Token", "")
        return bool(token) and hmac.compare_digest(sent.encode(), token.encode())
