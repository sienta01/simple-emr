import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..audit import audit
from ..forms import PatientForm, SoapForm
from ..models import Diagnosis, Patient, Procedure, Visit
from ..permissions import can, require
from ..utils import CARD_PREFIX, parse_date_param, parse_int
from .common import doctors_qs, penunjang_context, queue_fingerprint, visit_list_context


# --- Pendaftaran -------------------------------------------------------------

@require('pendaftaran')
def pendaftaran_list(request):
    ctx = visit_list_context(request, Visit.objects.all())
    ctx.update(show_doctor=True, can_cancel=True, row_link=False)
    if request.GET.get('partial'):
        return render(request, 'core/visits/_list_body.html', ctx)
    ctx.update(doctors=doctors_qs(), form=PatientForm(), card_prefix=CARD_PREFIX)
    return render(request, 'core/pendaftaran/list.html', ctx)


@require('pendaftaran')
@require_POST
def api_register_visit(request):
    patient = Patient.objects.filter(pk=parse_int(request.POST.get('patient'))).first()
    if patient is None:
        return JsonResponse({'error': 'Pasien tidak ditemukan.'}, status=400)
    doctor = doctors_qs().filter(pk=parse_int(request.POST.get('doctor'))).first()
    if doctor is None:
        return JsonResponse({'error': 'Pilih dokter terlebih dahulu.'}, status=400)

    today = timezone.localdate()
    waiting = Visit.objects.filter(patient=patient, doctor=doctor, tanggal=today, status=Visit.Status.BELUM).first()
    if waiting:
        return JsonResponse({
            'error': f'{patient.nama} sudah dalam antrian {doctor} hari ini (No. {waiting.no_antrian}).',
        }, status=400)

    visit = Visit.objects.create(patient=patient, doctor=doctor, tanggal=today, registered_by=request.user)
    audit(request, 'daftar_kunjungan', patient=patient, detail={'visit': visit.id, 'dokter': str(doctor)})
    messages.success(request, f'{patient.nama} terdaftar — antrian No. {visit.no_antrian} ({doctor}).')
    return JsonResponse({
        'ok': True,
        'no_antrian': visit.no_antrian,
        'redirect': reverse('pendaftaran') + '?status=belum',
    })


@require('pendaftaran')
@require_POST
def visit_cancel(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    if visit.status != Visit.Status.BELUM:
        return JsonResponse({'error': 'Hanya kunjungan yang belum diperiksa yang dapat dibatalkan.'}, status=400)
    visit.status = Visit.Status.BATAL
    visit.cancelled_at = timezone.now()
    visit.cancelled_by = request.user
    visit.save(update_fields=['status', 'cancelled_at', 'cancelled_by', 'updated_at'])
    audit(request, 'batal_kunjungan', patient=visit.patient, detail={'visit': visit.id})
    return JsonResponse({'ok': True})


# --- Pemeriksaan -------------------------------------------------------------

@require('pemeriksaan')
def pemeriksaan_list(request):
    is_doctor = request.user.has_role('dokter')
    dokter = request.GET.get('dokter') or ('saya' if is_doctor else 'semua')
    if not is_doctor:
        dokter = 'semua'
    base = Visit.objects.all()
    if dokter == 'saya':
        base = base.filter(doctor=request.user)
    ctx = visit_list_context(request, base)
    ctx.update(show_doctor=dokter != 'saya', can_cancel=False, row_link=True, dokter_filter=dokter, is_doctor=is_doctor)
    if request.GET.get('partial'):
        return render(request, 'core/visits/_list_body.html', ctx)
    return render(request, 'core/pemeriksaan/list.html', ctx)


def riwayat_for(patient, exclude=None):
    visits = (
        patient.visits.filter(status=Visit.Status.SUDAH)
        .select_related('doctor', 'examined_by')
        .prefetch_related('diagnoses', 'procedures')
        .order_by('-tanggal', '-examined_at')
    )
    if exclude is not None:
        visits = visits.exclude(pk=exclude.pk)
    return list(visits)


def with_procedures(riwayat):
    """The Tindakan history lists only visits that recorded a procedure."""
    return [v for v in riwayat if v.procedures.all()]


def riwayat_json(riwayat):
    return [
        {
            'id': v.id,
            's': v.subjective, 'o': v.objective, 'a': v.assessment, 'p': v.plan,
            'dx': [d.as_dict() for d in v.diagnoses.all()],
            'px': [p.as_dict() for p in v.procedures.all()],
        }
        for v in riwayat
    ]


@require('pemeriksaan')
def pemeriksaan_detail(request, pk):
    visit = get_object_or_404(Visit.objects.select_related('patient', 'doctor', 'examined_by'), pk=pk)
    patient = visit.patient
    can_write = can(request.user, 'soap_write') and visit.status != Visit.Status.BATAL

    if request.method == 'POST':
        if not can_write:
            raise PermissionDenied
        form = SoapForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                visit.subjective = data['subjective']
                visit.objective = data['objective']
                visit.assessment = data['assessment']
                visit.plan = data['plan']
                visit.status = Visit.Status.SUDAH
                visit.examined_at = timezone.now()
                visit.examined_by = request.user
                visit.save()
                visit.diagnoses.all().delete()
                Diagnosis.objects.bulk_create([
                    Diagnosis(visit=visit, code=d['code'], name=d['name'], urutan=i)
                    for i, d in enumerate(data['diagnoses'])
                ])
                visit.procedures.all().delete()
                Procedure.objects.bulk_create([
                    Procedure(visit=visit, code=p['code'], name=p['name'], urutan=i)
                    for i, p in enumerate(data['procedures'])
                ])
            # The full note goes into the audit trail, so every revision stays traceable.
            audit(request, 'simpan_soap', patient=patient, detail={
                'visit': visit.id, 'S': visit.subjective, 'O': visit.objective, 'A': visit.assessment,
                'ICD': data['diagnoses'], 'P': visit.plan, 'ICD9': data['procedures'],
            })
            messages.success(request, f'Pemeriksaan {patient.nama} tersimpan.')
            return redirect(f"{reverse('pemeriksaan')}?tanggal={visit.tanggal.isoformat()}")
    else:
        if can_write and visit.status == Visit.Status.BELUM and visit.called_at is None:
            # Opening the patient is how the doctor calls them in; the front desk list shows it.
            visit.called_at = timezone.now()
            visit.called_by = request.user
            visit.save(update_fields=['called_at', 'called_by', 'updated_at'])
            audit(request, 'panggil_pasien', patient=patient, detail={'visit': visit.id})
        form = SoapForm(readonly=not can_write, initial={
            'subjective': visit.subjective,
            'objective': visit.objective,
            'assessment': visit.assessment,
            'plan': visit.plan,
            'diagnoses': json.dumps([d.as_dict() for d in visit.diagnoses.all()]),
            'procedures': json.dumps([p.as_dict() for p in visit.procedures.all()]),
        })
        audit(request, 'buka_pemeriksaan', patient=patient, detail={'visit': visit.id})

    riwayat = riwayat_for(patient, exclude=visit)
    ctx = penunjang_context(request, patient, visit=visit)
    ctx.update(
        visit=visit,
        form=form,
        can_write=can_write,
        riwayat=riwayat,
        riwayat_tindakan=with_procedures(riwayat),
        riwayat_data=riwayat_json(riwayat),
        back_url=f"{reverse('pemeriksaan')}?tanggal={visit.tanggal.isoformat()}",
    )
    return render(request, 'core/pemeriksaan/detail.html', ctx)


@require('soap_write')
@require_POST
def visit_uncall(request, pk):
    """Undo a call (wrong patient opened, or the patient is not in the room yet)."""
    visit = get_object_or_404(Visit, pk=pk)
    if visit.is_called:
        visit.called_at = None
        visit.called_by = None
        visit.save(update_fields=['called_at', 'called_by', 'updated_at'])
        audit(request, 'batal_panggil', patient=visit.patient, detail={'visit': visit.id})
        messages.success(request, f'{visit.patient.nama} dikembalikan ke antrian.')
    return redirect(f"{reverse('pemeriksaan')}?tanggal={visit.tanggal.isoformat()}")


@require_GET
def queue_version(request):
    """Tiny fingerprint of one day's queue; the list pages poll it and reload only when it changes."""
    if not (can(request.user, 'pendaftaran') or can(request.user, 'pemeriksaan')):
        raise PermissionDenied
    tanggal = parse_date_param(request.GET.get('tanggal')) or timezone.localdate()
    return JsonResponse({'v': queue_fingerprint(tanggal)})
