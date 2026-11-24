"""Roles and what each may do. This is the only file to edit to add, rename or remove a role.

- ROLES lists the roles offered on the Pengguna form (key -> label, short description).
  A Django group is created automatically for every key when the app starts (core.apps).
- PERMS says which roles may do what. When you add a role, add its key to the sets it needs;
  when you remove one, delete it here too (`manage.py check` warns about leftovers).
- "admin" and "dokter" are built in (user management, the doctor list) and must stay.

Rebuild after editing: `docker compose up -d --build`.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied

ROLES = {
    'admin': ('Admin', 'kelola pengguna & data master'),
    'dokter': ('Dokter', 'mengisi SOAP'),
    'perawat': ('Perawat', 'antrian, alergi, penunjang'),
    'pendaftaran': ('Pendaftaran', 'daftar pasien'),
}
REQUIRED_ROLES = ('admin', 'dokter')
ALL_ROLES = frozenset(ROLES)

PERMS = {
    'pendaftaran': {'admin', 'pendaftaran', 'perawat'},   # register patients, manage the queue
    'pemeriksaan': {'admin', 'dokter', 'perawat'},        # open the examination queue
    'soap_write': {'dokter'},                             # write SOAP / ICD-X, call patients
    'clinical_read': {'admin', 'dokter', 'perawat'},      # read SOAP history
    'rekam_medis': ALL_ROLES,                             # patient database
    'patient_edit': ALL_ROLES,                            # edit identity data
    'alergi_edit': {'admin', 'dokter', 'perawat'},
    'penunjang': {'admin', 'dokter', 'perawat'},          # view/upload ancillary results, phone upload page
    'laporan': {'admin', 'dokter', 'perawat'},            # monthly DM/HT report for the Puskesmas (Excel)
    'users': {'admin'},
}


def can(user, perm):
    roles = PERMS[perm] & ALL_ROLES  # a role deleted from ROLES grants nothing, even if left in PERMS
    return bool(user is not None and user.is_authenticated and user.has_role(*roles))


def require(perm):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can(request.user, perm):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
