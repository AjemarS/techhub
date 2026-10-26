import hmac

from django.conf import settings


def is_internal_service(request):
    """
    The request carries the shared X-Internal-Token: another TechHub service (order-service for guests and
    webhooks, the frontend's server-side rendering). The gateway blanks this header on client requests.
    Compared in constant time; an unset token never matches.
    """
    token = getattr(settings, "INTERNAL_SERVICE_TOKEN", "")
    sent = request.headers.get("X-Internal-Token", "")
    return bool(token) and hmac.compare_digest(sent.encode(), token.encode())
