from rest_framework.permissions import BasePermission

from shared_auth.internal import is_internal_service
from shared_auth.permissions import IsAdminOrWarehouseWorker  # noqa: F401


class IsAuthenticatedOrInternalService(BasePermission):
    """A signed-in user, or another service carrying the shared X-Internal-Token (guest orders, webhooks)."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated) or is_internal_service(request)
