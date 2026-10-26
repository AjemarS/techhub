from rest_framework import throttling

from .internal import is_internal_service


class _InternalExempt:
    # Server-side rendering reaches the API from one address for every visitor; it is not one client
    def allow_request(self, request, view):
        return is_internal_service(request) or super().allow_request(request, view)


class AnonRateThrottle(_InternalExempt, throttling.AnonRateThrottle):
    pass


class UserRateThrottle(_InternalExempt, throttling.UserRateThrottle):
    pass
