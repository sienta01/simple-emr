from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponsePermanentRedirect
from django.http.request import split_domain_port


class HttpsRedirectMiddleware:
    """With the bundled Caddy (EMR_HOST), plain-HTTP visits to one of its addresses, such as an old
    http://server:8765 bookmark, are sent to the same page over HTTPS."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.hosts = {host.lower() for host in settings.EMR_HOSTS}
        if not self.hosts:
            raise MiddlewareNotUsed

    def __call__(self, request):
        # The container health check calls /healthz over plain HTTP from inside; leave it alone.
        if not request.is_secure() and request.path != '/healthz':
            host, _port = split_domain_port(request.get_host())
            if host in self.hosts:
                return HttpResponsePermanentRedirect(f'https://{host}{request.get_full_path()}')
        return self.get_response(request)
