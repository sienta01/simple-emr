from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.forms.models import model_to_dict
from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone

from ..forms import PatientForm
from ..models import Penunjang, User, Visit
from ..utils import parse_date_param

STATUS_TABS = [
    ('belum', 'Belum diperiksa'),
    ('sudah', 'Sudah diperiksa'),
    ('semua', 'Semua'),
]


def visit_list_context(request, base_qs):
    """Shared by the Pendaftaran and Pemeriksaan queues: one day, filtered by status tab."""
    tanggal = parse_date_param(request.GET.get('tanggal')) or timezone.localdate()
    status = request.GET.get('status') or 'belum'
    if status not in dict(STATUS_TABS):
        status = 'belum'

    day_qs = base_qs.filter(tanggal=tanggal)
    counts = {key: 0 for key in Visit.Status.values}
    for row in day_qs.values('status').annotate(n=Count('id')):
        counts[row['status']] = row['n']
    counts['semua'] = sum(counts.values())

    visits = day_qs.select_related('patient', 'doctor').order_by('no_antrian')
    if status != 'semua':
        visits = visits.filter(status=status)

    return {
        'tanggal': tanggal,
        'version': queue_fingerprint(tanggal),
        'is_today': tanggal == timezone.localdate(),
        'prev_date': tanggal - timedelta(days=1),
        'next_date': tanggal + timedelta(days=1),
        'status': status,
        'tabs': [(key, label, counts[key]) for key, label in STATUS_TABS],
        'visits': visits,
    }


def queue_fingerprint(tanggal):
    """Changes whenever a visit of that day is added or changed (registered, called, examined, cancelled)."""
    agg = Visit.objects.filter(tanggal=tanggal).aggregate(n=Count('id'), last=Max('updated_at'))
    last = agg['last'].timestamp() if agg['last'] else 0
    return f"{agg['n']}-{last:.6f}"


def doctors_qs():
    return User.objects.filter(groups__name='dokter', is_active=True).order_by('display_name', 'username').distinct()


def todays_visits(patients):
    """Today's open registrations per patient id, so the registration dialog can warn about doubles."""
    today = defaultdict(list)
    visits = (
        Visit.objects.filter(patient__in=patients, tanggal=timezone.localdate())
        .exclude(status=Visit.Status.BATAL)
        .select_related('doctor')
    )
    for v in visits:
        today[v.patient_id].append({
            'no_antrian': v.no_antrian, 'dokter': str(v.doctor), 'status': v.status,
            'status_label': v.get_status_display(),
        })
    return today


def patient_json(patient, today_visits=None):
    return {
        'id': patient.id,
        'no_rm': patient.no_rm,
        'kartu_url': reverse('kartu_cetak', args=[patient.pk]),
        'nama': patient.nama,
        'nik': patient.nik or '',
        'alamat': patient.alamat,
        'jk': patient.jenis_kelamin,
        'umur': patient.umur,
        'jk_umur': patient.jk_umur,
        'ttl': patient.ttl,
        'no_telp': patient.no_telp,
        'foto_url': patient.foto_url,
        'today': today_visits or [],
        # Current values keyed by form field, so the registration dialog can open the edit form.
        'fields': model_to_dict(patient, fields=PatientForm.Meta.fields),
    }


def serve_file(field, content_type, filename=None):
    if not field:
        raise Http404
    try:
        handle = field.open('rb')
    except FileNotFoundError:
        raise Http404
    response = FileResponse(handle, content_type=content_type, filename=filename or '')
    # URLs carry a version (?v=...), so a long private cache is safe.
    response['Cache-Control'] = 'private, max-age=604800'
    return response


def form_errors_response(form):
    errors = {field: [e['message'] for e in errs] for field, errs in form.errors.get_json_data().items()}
    return JsonResponse({'errors': errors}, status=400)


def mobile_upload_url(request, patient):
    path = reverse('mobile_upload', args=[patient.pk])
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip('/') + path
    return request.build_absolute_uri(path)


def penunjang_context(request, patient, visit=None, compact=False):
    return {
        'patient': patient,
        'visit': visit,
        'compact': compact,
        'kategori_choices': Penunjang.Kategori.choices,
        'penunjang_count': patient.penunjang.active().count(),
        'mobile_url': mobile_upload_url(request, patient),
    }
