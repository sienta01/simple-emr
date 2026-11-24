from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..audit import audit
from ..forms import LaporanForm
from ..permissions import require
from ..reports import FAKTOR_RISIKO, XLSX_CONTENT_TYPE, dm_ht_rows, dm_ht_workbook, periode_label
from .common import doctors_qs


def default_params(request, doctors):
    # Reports are sent in the first days of the next month, so the previous month comes first.
    last_month = timezone.localdate().replace(day=1) - timedelta(days=1)
    doctor = doctors.filter(pk=request.user.pk).first() or doctors.first()
    return {
        'bulan': f'{last_month:%Y-%m}',
        'dokter': doctor.pk if doctor else '',
        'puskesmas': settings.PUSKESMAS_NAME,
        'usia_min': 15,
        'usia_max': 59,
    }


@require('laporan')
def laporan(request):
    """DM / HT patients of one doctor and month, for the Puskesmas; ?unduh=xlsx downloads the Excel file."""
    doctors = doctors_qs()
    # An empty field falls back to its default, e.g. a link saved before PUSKESMAS_NAME was set.
    params = {key: value for key, value in request.GET.items() if value.strip()}
    form = LaporanForm({**default_params(request, doctors), **params}, doctors=doctors)
    ctx = {'form': form, 'faktor_risiko': FAKTOR_RISIKO, 'has_doctors': doctors.exists()}
    if not form.is_valid():
        return render(request, 'core/laporan/dm_ht.html', ctx)

    data = form.cleaned_data
    year, month = data['bulan']
    rows, no_birth_date = dm_ht_rows(data['dokter'], year, month, data['usia_min'], data['usia_max'])

    if request.GET.get('unduh'):
        content = dm_ht_workbook(
            rows, puskesmas=data['puskesmas'], dpm=str(data['dokter']), year=year, month=month,
            age_min=data['usia_min'], age_max=data['usia_max'],
        )
        audit(request, 'unduh_laporan', detail={
            'laporan': 'DM/HT', 'bulan': f'{year}-{month:02d}', 'dokter': str(data['dokter']), 'pasien': len(rows),
        })
        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response['Content-Disposition'] = f'attachment; filename="laporan-dm-ht-{year}-{month:02d}.xlsx"'
        response['Cache-Control'] = 'no-store'
        return response

    jk = Counter(row.patient.jenis_kelamin for row in rows)
    faktor = Counter(label for row in rows for label in row.faktor)
    ctx.update(
        rows=rows,
        no_birth_date=no_birth_date,
        periode=periode_label(year, month),
        counts={'L': jk['L'], 'P': jk['P'], 'faktor': [(label, faktor[label]) for label in FAKTOR_RISIKO]},
    )
    return render(request, 'core/laporan/dm_ht.html', ctx)
