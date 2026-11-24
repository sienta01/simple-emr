"""Phone pages. They only do one job: photograph ancillary results into a patient's chart."""
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..models import Patient, Visit
from ..permissions import require
from .common import penunjang_context


@require('penunjang')
def mobile_home(request):
    visits = (
        Visit.objects.filter(tanggal=timezone.localdate())
        .exclude(status=Visit.Status.BATAL)
        .select_related('patient', 'doctor')
        .order_by('no_antrian')
    )
    # A patient seeing two doctors today is still one place to upload to.
    seen, unique = set(), []
    for visit in visits:
        if visit.patient_id not in seen:
            seen.add(visit.patient_id)
            unique.append(visit)
    return render(request, 'core/mobile/home.html', {'visits': unique})


@require('penunjang')
def mobile_upload(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    return render(request, 'core/mobile/upload.html', penunjang_context(request, patient, compact=True))
