from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .utils import client_ip


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    AuditLog.objects.create(user=user, username=user.get_username(), action='login', ip=client_ip(request))


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is not None:
        AuditLog.objects.create(user=user, username=user.get_username(), action='logout', ip=client_ip(request))


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request=None, **kwargs):
    # Also feeds the lockout check in core.views.auth.LockoutMixin.
    AuditLog.objects.create(
        username=(credentials.get('username') or '')[:150],
        action='login_gagal',
        ip=client_ip(request),
    )
