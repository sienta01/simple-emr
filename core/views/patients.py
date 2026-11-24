import re

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from ..audit import audit
from ..forms import PatientForm, validate_nik
from ..imaging import UploadError, process_photo
from ..models import Icd9, Icd10, Patient
from ..permissions import can, require
from ..utils import card_qr, parse_card_code, search_patients
from .common import form_errors_response, patient_json, penunjang_context, serve_file, todays_visits
from .visits import riwayat_for, with_procedures

# "No. RM" sorts by registration time: new RM numbers follow it anyway, while imported MedPP numbers
# (name initial + count, e.g. S1001) would put old patients first when sorted as text.
RM_SORTS = {
    'rm_desc': ['-created_at', '-id'],
    'rm': ['created_at', 'id'],
    'nama': ['nama', 'no_rm'],
    'nama_desc': ['-nama', 'no_rm'],
}


@require('rekam_medis')
def rm_list(request):
    q = request.GET.get('q', '').strip()
    sort = request.GET.get('sort') if request.GET.get('sort') in RM_SORTS else 'rm_desc'
    patients = Patient.objects.all()
    if q:
        patients = search_patients(patients, q)
    paginator = Paginator(patients.order_by(*RM_SORTS[sort]), 50)
    ctx = {
        'page_obj': paginator.get_page(request.GET.get('page')),
        'total': paginator.count,
        'q': q,
        'sort': sort,
        'rm_next': 'rm' if sort == 'rm_desc' else 'rm_desc',
        'nama_next': 'nama_desc' if sort == 'nama' else 'nama',
    }
    if request.GET.get('partial'):  # search-as-you-type swaps only the table
        return render(request, 'core/rekam_medis/_results.html', ctx)
    return render(request, 'core/rekam_medis/list.html', ctx)


@require('rekam_medis')
def rm_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    ctx = penunjang_context(request, patient)
    if can(request.user, 'clinical_read'):
        ctx['riwayat'] = riwayat_for(patient)
        ctx['riwayat_tindakan'] = with_procedures(ctx['riwayat'])
    else:
        ctx['kunjungan'] = patient.visits.select_related('doctor').order_by('-tanggal', '-no_antrian')[:200]
    audit(request, 'buka_rm', patient=patient)
    return render(request, 'core/rekam_medis/detail.html', ctx)


@require('patient_edit')
def rm_edit(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'ubah_pasien', patient=patient, detail={'fields': form.changed_data})
        messages.success(request, 'Data pasien tersimpan.')
        return redirect('rm_detail', pk=patient.pk)
    return render(request, 'core/rekam_medis/edit.html', {'patient': patient, 'form': form})


@require('rekam_medis')
@require_GET
def api_patient_search(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    patients = list(search_patients(Patient.objects.all(), q).order_by('nama', 'no_rm')[:30])
    today = todays_visits(patients)
    return JsonResponse({'results': [patient_json(p, today[p.id]) for p in patients]})


@require('rekam_medis')
@require_GET
def api_patient_by_card(request):
    """The patient whose card was scanned at registration (webcam or USB scanner): exact RM match."""
    no_rm = parse_card_code(request.GET.get('kode'))
    if not no_rm:
        return JsonResponse({'error': 'QR ini bukan kartu periksa.'}, status=400)
    patient = Patient.objects.filter(no_rm=no_rm).first() or Patient.objects.filter(no_rm__iexact=no_rm).first()
    if patient is None:
        return JsonResponse({'error': f'Kartu No. RM {no_rm} tidak ditemukan di data pasien.'}, status=404)
    return JsonResponse({'patient': patient_json(patient, todays_visits([patient])[patient.id])})


@require('rekam_medis')
@require_GET
def kartu_cetak(request, pk):
    """Printable patient card (credit-card size) with the patient's data and a QR code of the RM number."""
    patient = get_object_or_404(Patient, pk=pk)
    qr = card_qr(patient.no_rm)
    audit(request, 'cetak_kartu', patient=patient)
    return render(request, 'core/kartu/cetak.html', {
        'patient': patient,
        'qr_svg': mark_safe(qr.svg_inline(scale=1, border=0, omitsize=True, dark='#000')),
        'kartu_nama': settings.KARTU_NAMA,
        'kartu_alamat': settings.KARTU_ALAMAT,
        'kartu_wa': settings.KARTU_WA,
        'autoprint': request.GET.get('cetak') == '1',
    })


@require('pendaftaran')
@require_POST
def api_patient_create(request):
    form = PatientForm(request.POST)
    if not form.is_valid():
        return form_errors_response(form)
    patient = form.save(commit=False)
    patient.created_by = request.user
    patient.save()
    audit(request, 'pasien_baru', patient=patient)
    return JsonResponse({'patient': patient_json(patient)})


@require('patient_edit')
@require_POST
def api_patient_update(request, pk):
    """Corrects identity data from the registration dialog, before the patient is registered."""
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(request.POST, instance=patient)
    if not form.is_valid():
        return form_errors_response(form)
    form.save()
    audit(request, 'ubah_pasien', patient=patient, detail={'fields': form.changed_data})
    return JsonResponse({'patient': patient_json(patient)})


@require('rekam_medis')
@require_GET
def patient_foto(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    return serve_file(patient.foto, 'image/jpeg')


@require('patient_edit')
@require_POST
def api_patient_foto(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    upload = request.FILES.get('foto')
    if upload is None:
        return JsonResponse({'error': 'Tidak ada foto.'}, status=400)
    if upload.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return JsonResponse({'error': f'File terlalu besar (maks {settings.MAX_UPLOAD_MB} MB).'}, status=400)
    try:
        data = process_photo(upload)
    except UploadError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    old = patient.foto.name
    patient.foto.save('foto.jpg', ContentFile(data), save=False)
    patient.save(update_fields=['foto', 'updated_at'])
    if old:  # nothing else refers to the replaced photo
        patient.foto.storage.delete(old)
    audit(request, 'foto_pasien', patient=patient)
    return JsonResponse({'foto_url': patient.foto_url})


@require('patient_edit')
@require_POST
def api_patient_foto_delete(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    old = patient.foto.name
    if old:
        patient.foto = ''
        patient.save(update_fields=['foto', 'updated_at'])
        patient.foto.storage.delete(old)
        audit(request, 'hapus_foto_pasien', patient=patient)
    return JsonResponse({'foto_url': ''})


@require('pendaftaran')
@require_POST
def api_patient_nik(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    nik = request.POST.get('nik', '').replace(' ', '').strip()
    try:
        validate_nik(nik, exclude_pk=patient.pk)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=400)
    patient.nik = nik
    patient.save(update_fields=['nik', 'updated_at'])
    audit(request, 'isi_nik', patient=patient)
    return JsonResponse({'patient': patient_json(patient)})


@require('alergi_edit')
@require_POST
def api_patient_alergi(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    alergi = ' '.join(request.POST.get('alergi', '').split())[:500]
    if alergi != patient.alergi:
        audit(request, 'ubah_alergi', patient=patient, detail={'dari': patient.alergi, 'menjadi': alergi})
        patient.alergi = alergi
        patient.save(update_fields=['alergi', 'updated_at'])
    return JsonResponse({'ok': True, 'alergi': patient.alergi, 'has_allergy': patient.has_allergy})


ICD_NODOT = re.compile(r'^([A-Za-z]\d{2})(\d{1,2})$')
ICD9_NODOT = re.compile(r'^(\d{2})(\d{1,2})$')


def code_search(model, q, nodot):
    """Code prefix ("J069" or "J06.9") or words of the title / hidden alias, codes that match first."""
    q = ' '.join(q.split())
    if not q:
        return JsonResponse({'results': []})
    code_q = q.upper()
    match = nodot.match(code_q)
    if match:  # "J069" -> "J06.9", "8659" -> "86.59"
        code_q = f'{match.group(1)}.{match.group(2)}'
    cond = Q(code__istartswith=code_q)
    words = Q()
    for word in q.split()[:5]:
        # English title or the hidden alias, so "hipertensi" still finds I10.
        words &= Q(name__icontains=word) | Q(alias__icontains=word)
    rows = list(model.objects.filter(cond | words)[:60])
    rows.sort(key=lambda r: (not r.code.upper().startswith(code_q), r.code))
    return JsonResponse({'results': [{'code': r.code, 'name': r.name} for r in rows[:15]]})


@require('pemeriksaan')
@require_GET
def api_icd10(request):
    return code_search(Icd10, request.GET.get('q', ''), ICD_NODOT)


@require('pemeriksaan')
@require_GET
def api_icd9(request):
    return code_search(Icd9, request.GET.get('q', ''), ICD9_NODOT)
