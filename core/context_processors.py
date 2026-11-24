from django.conf import settings
from django.urls import reverse

from .permissions import PERMS, can

NAV = [
    ('pendaftaran', 'pendaftaran', 'Pendaftaran', 'user-plus'),
    ('pemeriksaan', 'pemeriksaan', 'Pemeriksaan', 'stethoscope'),
    ('rm_list', 'rekam_medis', 'Rekam Medis', 'folder'),
    ('laporan', 'laporan', 'Laporan', 'sheet'),
    ('user_list', 'users', 'Pengguna', 'users'),
]


def app(request):
    ctx = {'CLINIC_NAME': settings.CLINIC_NAME, 'MAX_UPLOAD_MB': settings.MAX_UPLOAD_MB}
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return ctx
    perms = {name: can(user, name) for name in PERMS}
    nav = []
    for url_name, perm, label, icon in NAV:
        if perms[perm]:
            url = reverse(url_name)
            nav.append({'url': url, 'label': label, 'icon': icon, 'active': request.path.startswith(url)})
    ctx.update(can=perms, nav=nav)
    return ctx
