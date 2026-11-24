"""Startup checks for core/permissions.py, so a typo there shows up in `docker compose logs` instead of silently."""
from django.core.checks import Error, Warning, register

from .permissions import PERMS, REQUIRED_ROLES, ROLES


@register()
def check_roles(app_configs, **kwargs):
    problems = []
    for key in REQUIRED_ROLES:
        if key not in ROLES:
            problems.append(Error(
                f'Role "{key}" was removed from ROLES but the app depends on it.',
                hint='Put it back in core/permissions.py (you can change its label).',
                id='core.E001',
            ))
    for perm, roles in PERMS.items():
        for key in sorted(set(roles) - set(ROLES)):
            problems.append(Warning(
                f'PERMS["{perm}"] mentions role "{key}", which is not in ROLES; it is ignored.',
                hint='Remove it from PERMS in core/permissions.py.',
                id='core.W001',
            ))
    return problems


def ensure_role_groups(**kwargs):
    """post_migrate hook: every role in ROLES gets its Django group. Removed roles keep theirs (harmless)."""
    from django.contrib.auth.models import Group

    for key in ROLES:
        Group.objects.get_or_create(name=key)
