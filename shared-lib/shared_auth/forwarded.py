class ForwardedPrefixMiddleware:
    """
    The gateway serves a service under a public prefix (inventory: /api/inventory/) but forwards the
    request as /api/. Links the service builds (pagination) use request.path, so it gets the public
    prefix back from X-Forwarded-Prefix; routing keeps using path_info and is unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prefix = request.META.get("HTTP_X_FORWARDED_PREFIX", "").rstrip("/")
        if prefix and request.path.startswith("/api/"):
            request.path = prefix + request.path[len("/api"):]
        return self.get_response(request)
