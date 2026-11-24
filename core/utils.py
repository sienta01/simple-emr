import re
from datetime import date

import segno
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

MOBILE_UA = re.compile(r'Mobi', re.I)
NO_ALLERGY = {'', '-', 'tidak', 'tidak ada', 'tak ada', 'none', 'nihil'}

# The QR code on the printed patient card holds "RM:" + the RM number, e.g. "RM:000123". The prefix
# tells a card apart from other QR codes, and from a typed search when a USB scanner types it.
CARD_PREFIX = 'RM:'


def card_code(no_rm):
    return f'{CARD_PREFIX}{no_rm}'


def card_qr(no_rm):
    """QR code for the patient card. Error correction is always H: about 30 % of the code can be chipped
    or worn away and it still reads. Up to 7-character RM numbers give version 1 (21 x 21 modules)."""
    return segno.make(card_code(no_rm), error='h', micro=False)


def parse_card_code(text):
    """RM number from a scanned card, or None. A scanner with Caps Lock on may send "rm:000123"."""
    text = (text or '').strip()
    if text[:len(CARD_PREFIX)].upper() != CARD_PREFIX:
        return None
    return text[len(CARD_PREFIX):].strip() or None


def format_age(dob, today=None):
    """Age the way it is written in Indonesian charts: '30 th', '2 th 3 bl', '5 bl', '12 hr'."""
    if not dob:
        return ''
    today = today or timezone.localdate()
    if dob > today:
        return ''
    months = (today.year - dob.year) * 12 + (today.month - dob.month)
    if today.day < dob.day:
        months -= 1
    years, rem = divmod(months, 12)
    if months < 1:
        return f'{(today - dob).days} hr'
    if years < 1:
        return f'{months} bl'
    if years < 5 and rem:
        return f'{years} th {rem} bl'
    return f'{years} th'


def age_years(dob, on):
    """Completed years on a given date, or None when the date of birth is unknown."""
    if not dob:
        return None
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def is_allergy(text):
    return (text or '').strip().lower() not in NO_ALLERGY


def parse_date_param(value):
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def human_size(num_bytes):
    if num_bytes < 1024:
        return f'{num_bytes} B'
    if num_bytes < 1024 * 1024:
        return f'{round(num_bytes / 1024)} KB'
    return f'{num_bytes / (1024 * 1024):.1f} MB'


def client_ip(request):
    if request is None:
        return None
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


def is_mobile(request):
    return bool(MOBILE_UA.search(request.META.get('HTTP_USER_AGENT', '')))


def search_patients(queryset, query):
    """Every word must match the name, address, RM number, NIK or phone number."""
    for term in query.split()[:6]:
        cond = Q(nama__icontains=term) | Q(alamat__icontains=term) | Q(no_rm__icontains=term)
        if term.isdigit():
            cond |= Q(nik__startswith=term) | Q(no_telp__contains=term)
        queryset = queryset.filter(cond)
    return queryset


def ddmmyyyy(value: date):
    return value.strftime('%d-%m-%Y') if value else ''
