"""Imports patients and visit notes from MedPP ("Program Pasien Praktek", a Visual FoxPro program)."""
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.dbf import read_dbf
from core.models import AuditLog, Counter, Diagnosis, Icd10, Patient, Visit
from core.utils import ddmmyyyy
from core.views.common import doctors_qs

SOURCE = 'medpp'
PATIENT_FIELDS = ['CNOMR', 'CNMPASIEN', 'CKELAMIN', 'DTGLAHIR', 'CAL1', 'CAL2', 'CKERJA', 'CALERGI', 'CNOTELP']
VISIT_FIELDS = [
    'CNOMR', 'TGL', 'COL_ID', 'ANAMNESA', 'PERIKSA', 'DIAGNOSA', 'PENGOBATAN', 'CKDSAKIT', 'CNMSAKIT',
    'CRUJUKKE', 'CRUJUKDR', 'CNMTERAPI', 'DTGJANJI', 'CKETJANJI', 'CFILEGBR', 'CKETGBR',
]
REGISTRATION_FIELDS = ['CNOMR', 'DTGDAFTAR', 'CJAM']
# Control characters and the stray "Ÿ" (byte 0x9F) that MedPP leaves in some fields.
JUNK = re.compile('[\x00-\x1f\x7fŸ]')
NIK_LINE = re.compile(r'(?:NIK\s*[:;]?\s*)?(\d[\d .-]*\d)', re.I)
EARLIEST = date(1900, 1, 1)


def clean(text):
    return ' '.join(JUNK.sub('', text).split())


def split_nik(lines):
    """MedPP has no NIK field, so staff typed it as an address line ("9999 04 410190 0002", "NIK: 9999...").

    Returns (nik, the other lines), or (None, lines) when no line is exactly a 16-digit number.
    """
    for i, line in enumerate(lines):
        match = NIK_LINE.fullmatch(line)
        digits = re.sub(r'\D', '', match.group(1)) if match else ''
        if len(digits) == 16:
            return digits, lines[:i] + lines[i + 1:]
    return None, lines


def parse_date(value, latest):
    """YYYYMMDD -> date, or None when empty, malformed or outside 1900..latest."""
    try:
        parsed = datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        return None
    return parsed if EARLIEST <= parsed <= latest else None


def find_table(folder, name, required=True):
    """Case-insensitive, since the files come from Windows (PASIEN.DBF, pasien.dbf...)."""
    for path in folder.iterdir():
        if path.name.lower() == name:
            return path
    if required:
        raise CommandError(f'{name} not found in {folder}')
    return None


class Command(BaseCommand):
    help = (
        'Imports patients (pasien.dbf) and visit notes (rm.dbf) from a MedPP folder, keeping the old RM numbers. '
        'Can be run again later: patients already present and visits imported before are skipped.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'folder', help='Folder containing pasien.dbf and rm.dbf (and daftar.dbf for registration dates)',
        )
        parser.add_argument(
            '--dokter', metavar='USERNAME',
            help='Doctor the old visits are filed under (default: the only active user with the Dokter role)',
        )
        parser.add_argument('--icd', action='store_true', help='Also add the ICD-10 codes from ICD.DBF that are missing')
        parser.add_argument('--dry-run', action='store_true', help='Check everything and report, but save nothing')

    def handle(self, *args, folder, dokter=None, icd=False, dry_run=False, **options):
        folder = Path(folder)
        if not folder.is_dir():
            raise CommandError(f'Folder not found: {folder}')
        doctor = self.get_doctor(dokter)
        today = timezone.localdate()
        self.issues = defaultdict(list)
        self.first_seen = {}

        try:
            patients = self.read_patients(find_table(folder, 'pasien.dbf'), today)
            visits, empty = self.read_visits(find_table(folder, 'rm.dbf'), today)
            registrations = find_table(folder, 'daftar.dbf', required=False)
            if registrations:
                self.read_registrations(registrations, today)
            else:
                self.stdout.write('daftar.dbf not found: registration dates are taken from the visit notes only.')
            codes = self.read_icd(find_table(folder, 'icd.dbf')) if icd else {}
        except ValueError as exc:  # not a DBF file, or not MedPP's columns
            raise CommandError(exc) from exc

        with transaction.atomic():
            stats = self.save(patients, self.registered_at(patients), visits, codes, doctor)
            stats.update(empty_rows=empty, doctor=doctor.username, folder=str(folder))
            AuditLog.objects.create(action='import_medpp', detail=json.dumps(stats, ensure_ascii=False))
            if dry_run:
                transaction.set_rollback(True)
        self.report(stats, dry_run)

    @staticmethod
    def get_doctor(username):
        doctors = doctors_qs()
        if username:
            doctor = doctors.filter(username=username).first()
            if doctor is None:
                raise CommandError(f'"{username}" is not an active user with the Dokter role.')
            return doctor
        found = list(doctors[:2])
        if len(found) == 1:
            return found[0]
        names = ', '.join(doctors.values_list('username', flat=True)) or 'none yet, create one under Pengguna first'
        raise CommandError(f'Choose the doctor with --dokter USERNAME. Doctors: {names}.')

    def read_patients(self, path, today):
        """{no_rm: (Patient, [problems])}; problems are only reported for patients actually created."""
        rows = {}
        for row in read_dbf(path, PATIENT_FIELDS):
            row = {key: clean(value) for key, value in row.items()}
            no_rm = row['CNOMR']
            if not no_rm or len(no_rm) > 20 or no_rm in rows:
                self.issues['Patients skipped (RM number empty, too long or repeated)'].append(no_rm or '(empty)')
                continue
            rows[no_rm] = row
        # NIK is unique: one found on several patients (often a family sharing one) stays in the address.
        found = defaultdict(int)
        for row in rows.values():
            found[split_nik([row['CAL1'], row['CAL2']])[0]] += 1
        taken = dict(Patient.objects.exclude(nik=None).values_list('nik', 'no_rm'))

        patients = {}
        for no_rm, row in rows.items():
            problems = []
            nik, lines = split_nik([row['CAL1'], row['CAL2']])
            if nik and (found[nik] > 1 or taken.get(nik, no_rm) != no_rm):
                problems.append(('NIK also found on another patient, left in the address', f'{no_rm} {nik}'))
                nik, lines = None, [row['CAL1'], row['CAL2']]
            dob = parse_date(row['DTGLAHIR'], today)
            if dob is None:
                problems.append(('Date of birth empty or invalid, left empty', f'{no_rm} ({row["DTGLAHIR"] or "-"})'))
            sex = row['CKELAMIN'].upper()
            if sex not in Patient.JenisKelamin.values:
                sex = ''
                problems.append(('Sex not recorded', no_rm))
            alamat = ', '.join(line for line in lines if line)
            if not alamat:
                problems.append(('Address empty', no_rm))
            patient = Patient(
                no_rm=no_rm, nik=nik, nama=row['CNMPASIEN'][:150], jenis_kelamin=sex, tanggal_lahir=dob,
                alamat=alamat[:255], pekerjaan=row['CKERJA'][:100], alergi=row['CALERGI'], no_telp=row['CNOTELP'][:30],
            )
            patients[no_rm] = (patient, problems)
        return patients

    def read_visits(self, path, today):
        visits, empty = [], 0
        for row in read_dbf(path, VISIT_FIELDS):
            row = {key: clean(value) for key, value in row.items()}
            no_rm = row['CNOMR']
            try:
                entered = datetime.strptime(row['COL_ID'][:17], '%Y%m%d-%H:%M:%S')
            except ValueError:
                entered = None
            self.seen(no_rm, entered)
            # MedPP's extra fields have no column of their own here; they are kept as lines under P.
            plan = [row['PENGOBATAN']]
            rujuk = ' / '.join(part for part in (row['CRUJUKKE'], row['CRUJUKDR']) if part)
            if rujuk:
                plan.append(f'Rujuk: {rujuk}')
            if row['CNMTERAPI']:
                plan.append(f'Terapi: {row["CNMTERAPI"]}')
            kontrol = ' '.join(part for part in (ddmmyyyy(parse_date(row['DTGJANJI'], date.max)), row['CKETJANJI']) if part)
            if kontrol:
                plan.append(f'Kontrol: {kontrol}')
            lampiran = ' '.join(part for part in (row['CFILEGBR'], row['CKETGBR']) if part)
            if lampiran:
                plan.append(f'Lampiran: {lampiran}')
            soap = (row['ANAMNESA'], row['PERIKSA'], row['DIAGNOSA'], '\n'.join(line for line in plan if line))
            dx = (row['CKDSAKIT'].upper()[:10], row['CNMSAKIT'][:300])
            if not any(soap) and not any(dx):
                empty += 1  # MedPP creates a blank row at registration; nothing was written in it
                continue

            problems = []
            tanggal = parse_date(row['TGL'], today)
            if tanggal is None:
                if entered is None:
                    self.issues['Visits skipped (no valid date)'].append(f'{no_rm} ({row["TGL"] or "-"})')
                    continue
                tanggal = entered.date()
                problems.append(('Visit date invalid, date of entry used', f'{no_rm} {row["TGL"] or "-"} -> {tanggal}'))
            # TGL is the visit date (old paper notes were typed in later); COL_ID's clock time keeps the day's order.
            at = timezone.make_aware(datetime.combine(tanggal, entered.time() if entered else time(12)))
            if row['COL_ID']:
                legacy_id = f'{SOURCE}:{row["COL_ID"]}'
            else:
                digest = hashlib.sha1('\n'.join(soap).encode()).hexdigest()[:12]
                legacy_id = f'{SOURCE}:{no_rm}:{tanggal:%Y%m%d}:{digest}'
            visits.append({
                'no_rm': no_rm, 'tanggal': tanggal, 'at': at, 'soap': soap, 'dx': dx,
                'legacy_id': legacy_id[:100], 'problems': problems,
            })
        visits.sort(key=lambda v: (v['at'], v['legacy_id']))
        return visits, empty

    def read_registrations(self, path, today):
        """daftar.dbf is MedPP's registration log; its first entry per patient is when they were registered."""
        for row in read_dbf(path, REGISTRATION_FIELDS):
            day = parse_date(row['DTGDAFTAR'], today)
            if day is None:
                continue
            try:
                clock = datetime.strptime(row['CJAM'][:8], '%H:%M:%S').time()
            except ValueError:
                clock = time()
            self.seen(clean(row['CNOMR']), datetime.combine(day, clock))

    def seen(self, no_rm, moment):
        if moment and (no_rm not in self.first_seen or moment < self.first_seen[no_rm]):
            self.first_seen[no_rm] = moment

    def registered_at(self, patients):
        """{no_rm: aware datetime} for created_at, so newest-first lists mix old and new patients correctly.

        pasien.dbf is in registration order, so a patient MedPP has no date for takes the one before it.
        """
        times, last = {}, None
        for no_rm in patients:
            last = self.first_seen.get(no_rm, last)
            if last:
                times[no_rm] = timezone.make_aware(last)
        return times

    @staticmethod
    def read_icd(path):
        codes = {}
        for row in read_dbf(path, ['CKDSAKIT', 'CNMSAKIT']):
            code, name = clean(row['CKDSAKIT']).upper(), clean(row['CNMSAKIT'])
            if code and name:
                codes[code[:10]] = name[:300]
        return codes

    def save(self, patients, registered, visits, codes, doctor):
        existing = set(Patient.objects.values_list('no_rm', flat=True))
        new_patients = []
        for no_rm, (patient, problems) in patients.items():
            if no_rm not in existing:
                new_patients.append(patient)
                for label, detail in problems:
                    self.issues[label].append(detail)
        Patient.objects.bulk_create(new_patients, batch_size=500)
        # created_at is auto_now_add, so the insert always stamps "now"; MedPP's own date goes in afterwards.
        dated = [p for p in new_patients if p.no_rm in registered]
        for patient in dated:
            patient.created_at = registered[patient.no_rm]
        Patient.objects.bulk_update(dated, ['created_at'], batch_size=500)

        patient_ids = dict(Patient.objects.values_list('no_rm', 'id'))
        imported = set(Visit.objects.filter(legacy_id__startswith=f'{SOURCE}:').values_list('legacy_id', flat=True))
        new_visits, diagnoses, seen = [], [], 0
        for v in visits:
            if v['legacy_id'] in imported:
                seen += 1
                continue
            patient_id = patient_ids.get(v['no_rm'])
            if patient_id is None:
                self.issues['Visits skipped (RM number not in pasien.dbf)'].append(f'{v["no_rm"] or "(empty)"} {v["tanggal"]}')
                continue
            imported.add(v['legacy_id'])
            for label, detail in v['problems']:
                self.issues[label].append(detail)
            s, o, a, p = v['soap']
            visit = Visit(
                patient_id=patient_id, doctor=doctor, tanggal=v['tanggal'], status=Visit.Status.SUDAH,
                subjective=s, objective=o, assessment=a, plan=p,
                registered_at=v['at'], examined_at=v['at'], examined_by=doctor, legacy_id=v['legacy_id'],
            )
            new_visits.append(visit)
            code, name = v['dx']
            if code or name:
                diagnoses.append(Diagnosis(visit=visit, code=code, name=name or code))
        self.number_queue(new_visits)
        Visit.objects.bulk_create(new_visits, batch_size=500)
        Diagnosis.objects.bulk_create(diagnoses, batch_size=500)

        new_codes = []
        if codes:
            known = set(Icd10.objects.values_list('code', flat=True))
            new_codes = [Icd10(code=code, name=name) for code, name in codes.items() if code not in known]
            Icd10.objects.bulk_create(new_codes, batch_size=1000)

        return {
            'patients_read': len(patients), 'patients_new': len(new_patients),
            'visits_new': len(new_visits), 'visits_already_imported': seen,
            'icd_read': len(codes), 'icd_new': len(new_codes),
        }

    @staticmethod
    def number_queue(visits):
        """Numbers each day's imported visits after any already on that day, and moves the day counters on."""
        if not visits:
            return
        by_day = defaultdict(list)
        for visit in visits:
            by_day[visit.tanggal].append(visit)
        taken = dict(
            Visit.objects.filter(tanggal__gte=min(by_day)).order_by()
            .values('tanggal').annotate(last=Max('no_antrian')).values_list('tanggal', 'last')
        )
        counters = dict(
            Counter.objects.select_for_update().filter(key__startswith='antrian:').values_list('key', 'value')
        )
        updated = []
        for day, day_visits in by_day.items():
            key = f'antrian:{day.isoformat()}'
            number = max(taken.get(day, 0), counters.get(key, 0))
            for visit in day_visits:
                number += 1
                visit.no_antrian = number
            updated.append(Counter(key=key, value=number))
        Counter.objects.bulk_create(
            updated, batch_size=500, update_conflicts=True, unique_fields=['key'], update_fields=['value'],
        )

    def report(self, stats, dry_run):
        out = self.stdout.write
        out(self.style.WARNING('Dry run: nothing was saved.') if dry_run else self.style.SUCCESS('MedPP import done.'))
        out(f'  Patients: {stats["patients_new"]} new, '
            f'{stats["patients_read"] - stats["patients_new"]} already present (left unchanged)')
        out(f'  Visits:   {stats["visits_new"]} new, {stats["visits_already_imported"]} imported before, '
            f'{stats["empty_rows"]} empty rows skipped (filed under {stats["doctor"]})')
        if stats['icd_read']:
            out(f'  ICD-10:   {stats["icd_new"]} codes added, {stats["icd_read"] - stats["icd_new"]} already present')
        if self.issues:
            out('\nPlease check these records (fill in the missing data under Rekam Medis):')
            for label, details in self.issues.items():
                out(f'  {label} ({len(details)}):')
                lines = [[]]
                for detail in details:  # wrap between entries, never inside one
                    if lines[-1] and len(', '.join(lines[-1] + [detail])) > 94:
                        lines.append([])
                    lines[-1].append(detail)
                out(',\n'.join('    ' + ', '.join(line) for line in lines))
