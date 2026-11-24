"""Monthly DM / HT report for the Puskesmas ("jejaring fasyankes swasta"), shown on screen and as Excel.

A patient is listed when one of the doctor's finished visits in the month carries the ICD-X code of a
risk factor below and the patient was within the age range on the day of that visit. The risk factor
column is the patient's history: every such code up to the end of the month, from any doctor, so a
diabetic who came for hypertension this month is still "DM, HT".
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from django.db.models import Q
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .models import Diagnosis, Patient, Visit
from .utils import age_years

# Label in the report -> (ICD-X code prefix, name in the legend). E11 also covers E11.0-E11.9.
FAKTOR_RISIKO = {
    'DM': ('E11', 'Diabetes melitus'),
    'HT': ('I10', 'Hipertensi'),
}
BULAN = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
]
XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def format_nik(nik):
    """9999011503810001 -> '9999 01 150381 0001' (regency, district, birth date, serial); '-' when unknown."""
    if not nik:
        return '-'
    if len(nik) == 16 and nik.isdigit():
        return f'{nik[:4]} {nik[4:6]} {nik[6:12]} {nik[12:]}'
    return nik


def periode_label(year, month):
    return f'{BULAN[month - 1]} Thn {year}'


@dataclass
class ReportRow:
    patient: Patient
    faktor: list  # labels from FAKTOR_RISIKO, in that order

    @property
    def faktor_label(self):
        return ', '.join(self.faktor)

    @property
    def nik(self):
        return format_nik(self.patient.nik)


def _risk_codes():
    cond = Q()
    for prefix, _name in FAKTOR_RISIKO.values():
        cond |= Q(code__istartswith=prefix)
    return cond


def dm_ht_rows(doctor, year, month, age_min, age_max):
    """Returns the report rows (in order of the patient's first visit of the month) and the patients who
    would qualify but have no date of birth, so their age cannot be checked."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    finished = Q(visit__status=Visit.Status.SUDAH, visit__tanggal__lt=end)

    first_visit = {}
    month_dx = (
        Diagnosis.objects.filter(_risk_codes(), finished, visit__doctor=doctor, visit__tanggal__gte=start)
        .select_related('visit__patient')
        .order_by('visit__tanggal', 'visit__no_antrian')
    )
    for dx in month_dx:
        first_visit.setdefault(dx.visit.patient_id, dx.visit)

    patients, no_birth_date = [], []
    for visit in first_visit.values():
        age = age_years(visit.patient.tanggal_lahir, visit.tanggal)
        if age is None:
            no_birth_date.append(visit.patient)
        elif age_min <= age <= age_max:
            patients.append(visit.patient)

    history = defaultdict(set)
    codes = Diagnosis.objects.filter(
        _risk_codes(), finished, visit__patient__in=[p.id for p in patients],
    ).values_list('visit__patient_id', 'code')
    for patient_id, code in codes:
        code = code.strip().upper()
        history[patient_id].update(label for label, (prefix, _name) in FAKTOR_RISIKO.items() if code.startswith(prefix))
    rows = [ReportRow(p, [label for label in FAKTOR_RISIKO if label in history[p.id]]) for p in patients]
    return rows, no_birth_date


FONT = 'Arial'
THIN = Side(style='thin')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
YELLOW = PatternFill('solid', fgColor='FFFF00')
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)


def dm_ht_workbook(rows, *, puskesmas, dpm, year, month, age_min, age_max):
    """The .xlsx laid out like the Puskesmas form: title, DPM and month, then a three-row yellow header."""
    wb = Workbook()
    ws = wb.active
    ws.title = f'{BULAN[month - 1]} {year}'
    for col, width in zip('ABCDEFG', (5, 26, 22, 38, 5, 5, 32)):
        ws.column_dimensions[col].width = width

    ws.merge_cells('A1:G1')
    ws['A1'] = f'JEJARING FASYANKES SWASTA PUSKESMAS {puskesmas.upper()}'.strip()
    ws['A1'].font = Font(name=FONT, size=14, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center')
    for r, (label, value) in enumerate((('DPM', dpm), ('BULAN PELAPORAN', periode_label(year, month))), start=2):
        ws.cell(r, 1, label).font = Font(name=FONT, size=11)
        ws.cell(r, 3, value).font = Font(name=FONT, size=11)

    header = {
        'A4:A6': 'NO', 'B4:B6': 'NAMA', 'C4:C6': 'NIK', 'D4:D6': 'ALAMAT',
        'E4:G4': f'PENDUDUK USIA {age_min}-{age_max} TAHUN',
        'E5:F5': 'JENIS KELAMIN',
        'G5:G6': '*FAKTOR RESIKO/ RIWAYAT PENYAKIT (lihat jenis faktor resiko di bawah)',
        'E6': 'L', 'F6': 'P',
    }
    for ref, text in header.items():
        if ':' in ref:
            ws.merge_cells(ref)
        ws[ref.split(':')[0]] = text
    # Styled after merging, so every cell of a merged block gets its border and fill.
    for line in ws['A4:G6']:
        for cell in line:
            cell.fill, cell.border, cell.alignment, cell.font = YELLOW, BOX, CENTER, Font(name=FONT, size=11)
    ws.row_dimensions[5].height = 22
    ws.row_dimensions[6].height = 22

    body = Font(name=FONT, size=10)
    for no, row in enumerate(rows, start=1):
        p = row.patient
        values = [
            no, p.nama.upper(), row.nik, p.alamat.upper(),
            'L' if p.jenis_kelamin == 'L' else '', 'P' if p.jenis_kelamin == 'P' else '', row.faktor_label,
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(6 + no, col, value)
            cell.border, cell.font = BOX, body
            cell.alignment = CENTER if col >= 5 else LEFT
        ws.cell(6 + no, 3).number_format = '@'  # NIK stays text if someone retypes it

    r = 6 + len(rows) + 2
    ws.cell(r, 1, '*Jenis faktor resiko:').font = Font(name=FONT, size=10, bold=True)
    for label, (prefix, name) in FAKTOR_RISIKO.items():
        r += 1
        ws.cell(r, 2, f'{label} = {name} (ICD-X {prefix})').font = body

    ws.freeze_panes = 'A7'
    ws.print_title_rows = '4:6'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    out = BytesIO()
    wb.save(out)
    return out.getvalue()
