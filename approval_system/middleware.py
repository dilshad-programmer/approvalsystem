from django.conf import settings

class DisableCSRFMiddleware:
    """
    Middleware to globally exempt all views from CSRF verification.
    Required because the app runs on HTTP (not HTTPS) on Elastic Beanstalk.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Always disable CSRF — app runs on HTTP, not HTTPS
        setattr(request, '_dont_enforce_csrf_checks', True)
        response = self.get_response(request)
        return response

