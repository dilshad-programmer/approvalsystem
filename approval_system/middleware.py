from django.conf import settings

class DisableCSRFMiddleware:
    """
    Middleware to globally exempt all views from CSRF verification
    ONLY when DEBUG is True. This ensures local development is never
    blocked by token/cookie mismatches.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.DEBUG:
            # This attribute tells Django's CsrfViewMiddleware to skip verification
            setattr(request, '_dont_enforce_csrf_checks', True)
        
        response = self.get_response(request)
        return response
