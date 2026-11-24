import json
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_GET, require_POST

from ..audit import audit
from ..imaging import ROTATIONS, UploadError, process_upload, rotate_jpeg
from ..models import Patient, Penunjang, Visit
from ..permissions import require
from ..utils import human_size, parse_int
from .common import mobile_upload_url, serve_file


def item_json(p, page=None):
    version = int(p.updated_at.timestamp())
    return {
        'id': p.id,
        'page': page,
        'kategori': p.kategori,
        'kategori_label': p.get_kategori_display(),
        'is_pdf': p.is_pdf,
        'note': p.note,
        'size': human_size(p.size),
        'original_name': p.original_name,
        'file_url': f"{reverse('penunjang_file', args=[p.id])}?v={version}",
        'thumb_url': f"{reverse('penunjang_thumb', args=[p.id])}?v={version}" if p.thumbnail else None,
        'uploaded_at': date_format(timezone.localtime(p.uploaded_at), 'j M Y H:i'),
        'uploaded_by': str(p.uploaded_by) if p.uploaded_by else '',
    }


def _active_items(patient):
    return list(patient.penunjang.active().select_related('uploaded_by').order_by('position', 'id'))


@require('penunjang')
@require_GET
def penunjang_list(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    items = _active_items(patient)
    data = [item_json(p, page=i + 1) for i, p in enumerate(items)]
    data.reverse()  # newest page first
    return JsonResponse({'items': data})


@require('penunjang')
@require_POST
def penunjang_upload(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'error': 'Tidak ada file.'}, status=400)
    if upload.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return JsonResponse({'error': f'File terlalu besar (maks {settings.MAX_UPLOAD_MB} MB).'}, status=400)
    kategori = request.POST.get('kategori')
    if kategori not in Penunjang.Kategori.values:
        kategori = Penunjang.Kategori.LAINNYA

    try:
        processed = process_upload(upload)
    except UploadError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    # Attach to the visit being examined, or else to today's visit if there is one.
    visit = None
    visit_id = parse_int(request.POST.get('visit'))
    if visit_id:
        visit = Visit.objects.filter(pk=visit_id, patient=patient).first()
    if visit is None:
        visit = (
            patient.visits.filter(tanggal=timezone.localdate())
            .exclude(status=Visit.Status.BATAL).order_by('-registered_at').first()
        )

    with transaction.atomic():
        Patient.objects.select_for_update().filter(pk=patient.pk).first()  # serialise page numbering
        last = patient.penunjang.aggregate(m=Max('position'))['m'] or 0
        item = Penunjang(
            patient=patient,
            visit=visit,
            kategori=kategori,
            original_name=(upload.name or '')[:255],
            content_type=processed.content_type,
            size=len(processed.data),
            note=request.POST.get('note', '').strip()[:500],
            position=last + 1,
            uploaded_by=request.user,
        )
        item.file.save(f'page{processed.ext}', ContentFile(processed.data), save=False)
        if processed.thumb:
            item.thumbnail.save('thumb.jpg', ContentFile(processed.thumb), save=False)
        item.save()

    audit(request, 'upload_penunjang', patient=patient, detail={'id': item.id, 'kategori': kategori})
    return JsonResponse({'item': item_json(item, page=patient.penunjang.active().count())})


@require('penunjang')
@require_POST
def penunjang_update(request, pk):
    item = get_object_or_404(Penunjang.objects.active(), pk=pk)
    changes = {}
    if 'note' in request.POST:
        note = request.POST['note'].strip()[:500]
        if note != item.note:
            item.note = note
            changes['note'] = note
    kategori = request.POST.get('kategori')
    if kategori in Penunjang.Kategori.values and kategori != item.kategori:
        item.kategori = kategori
        changes['kategori'] = kategori
    rotate = parse_int(request.POST.get('rotate'))
    replaced_files = []
    if rotate and rotate in ROTATIONS and not item.is_pdf:
        with item.file.open('rb') as fh:
            data, thumb = rotate_jpeg(BytesIO(fh.read()), rotate)
        replaced_files = [name for name in (item.file.name, item.thumbnail.name) if name]
        item.file.save('page.jpg', ContentFile(data), save=False)
        item.thumbnail.save('thumb.jpg', ContentFile(thumb), save=False)
        item.size = len(data)
        changes['rotate'] = rotate
    if changes:
        item.save()
        audit(request, 'ubah_penunjang', patient=item.patient, detail={'id': item.id, **changes})
    for name in replaced_files:  # rotated copies replace the old ones; nothing else references them
        item.file.storage.delete(name)
    return JsonResponse({'item': item_json(item)})


@require('penunjang')
@require_POST
def penunjang_delete(request, pk):
    # Soft delete: the page disappears from the chart but stays on disk for medico-legal reasons.
    item = get_object_or_404(Penunjang.objects.active(), pk=pk)
    item.deleted_at = timezone.now()
    item.deleted_by = request.user
    item.save(update_fields=['deleted_at', 'deleted_by', 'updated_at'])
    audit(request, 'hapus_penunjang', patient=item.patient, detail={'id': item.id})
    return JsonResponse({'ok': True})


@require('penunjang')
@require_POST
def penunjang_reorder(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    try:
        order = [int(x) for x in json.loads(request.body or b'{}').get('order', [])]
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Urutan tidak valid.'}, status=400)
    items = {p.id: p for p in _active_items(patient)}
    if sorted(order) != sorted(items):
        return JsonResponse({'error': 'Daftar halaman sudah berubah. Muat ulang halaman.'}, status=409)
    # `order` is display order (newest first), so the first entry gets the highest position.
    total = len(order)
    for index, item_id in enumerate(order):
        items[item_id].position = total - index
    Penunjang.objects.bulk_update(items.values(), ['position'])
    audit(request, 'urut_penunjang', patient=patient)
    return JsonResponse({'ok': True})


@require('penunjang')
@require_GET
def penunjang_file(request, pk):
    item = get_object_or_404(Penunjang.objects.active(), pk=pk)
    filename = (item.original_name or 'penunjang.pdf') if item.is_pdf else None
    return serve_file(item.file, item.content_type, filename)


@require('penunjang')
@require_GET
def penunjang_thumb(request, pk):
    item = get_object_or_404(Penunjang.objects.active(), pk=pk)
    return serve_file(item.thumbnail, 'image/jpeg')


@require('penunjang')
@require_GET
def penunjang_qr(request, patient_id):
    import segno

    patient = get_object_or_404(Patient, pk=patient_id)
    buf = BytesIO()
    segno.make(mobile_upload_url(request, patient), error='m').save(buf, kind='svg', scale=6, border=2)
    return HttpResponse(buf.getvalue(), content_type='image/svg+xml')
