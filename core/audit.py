import json

from .models import AuditLog
from .utils import client_ip


def audit(request, action, patient=None, detail=''):
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    user = request.user if request.user.is_authenticated else None
    AuditLog.objects.create(
        user=user,
        username=user.get_username() if user else '',
        action=action,
        patient=patient,
        detail=detail,
        ip=client_ip(request),
    )
