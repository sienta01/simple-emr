"""Demo data for trying the app out. Never run this on a production database."""
from datetime import date, timedelta

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Diagnosis, Patient, Procedure, User, Visit

DEMO_PASSWORD = 'demo-klinik-2026'

USERS = [
    ('dokter', 'dr. Andi Pratama', ['dokter']),
    ('dokter2', 'dr. Sari Lestari', ['dokter']),
    ('perawat', 'Ns. Dewi Anggraini', ['perawat']),
    ('daftar', 'Rina (Pendaftaran)', ['pendaftaran']),
]

PATIENTS = [
    ('3372011201960001', 'Budi Santoso', 'L', 'Jakarta', date(1996, 1, 12), 'Karyawan swasta', 'Jl. Slamet Riyadi 21, Solo', '081234567801', '', '', 'Amoksisilin'),
    (None, 'Siti Rahmawati', 'P', 'Surakarta', date(1988, 7, 3), 'Guru', 'Jl. Adi Sucipto 5, Solo', '081234567802', 'Ahmad Fauzi', 'kerabat', ''),
    ('3372015505150003', 'Aisyah Putri', 'P', 'Solo', date(2022, 5, 15), '', 'Perum Fajar Indah C-12, Solo', '081234567803', 'Siti Rahmawati', 'ibu', 'Tidak ada'),
    (None, 'Hartono Wibisono', 'L', 'Klaten', date(1957, 11, 20), 'Pensiunan', 'Jl. Veteran 88, Klaten', '081234567804', 'Dimas Hartono', 'anak', ''),
    ('3372016406900005', 'Maria Natalia', 'P', 'Yogyakarta', date(1990, 6, 24), 'Wiraswasta', 'Jl. Kaliurang Km 7, Sleman', '', '', '', 'Debu, udang'),
    (None, 'Joko Susilo', 'L', 'Boyolali', date(1975, 3, 9), 'Petani', 'Desa Mojosongo RT 02/05, Boyolali', '081234567806', '', '', ''),
]


class Command(BaseCommand):
    help = 'Creates demo users, patients and visits. For evaluation only.'

    @transaction.atomic
    def handle(self, *args, **options):
        doctors = []
        for username, display, roles in USERS:
            user, created = User.objects.get_or_create(username=username, defaults={'display_name': display})
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
                user.groups.set(Group.objects.filter(name__in=roles))
            if 'dokter' in roles:
                doctors.append(user)

        if Patient.objects.filter(nama='Budi Santoso').exists():
            self.stdout.write('Demo patients already exist; users ensured.')
            return

        patients = [
            Patient.objects.create(
                nik=nik, nama=nama, jenis_kelamin=jk, tempat_lahir=tempat, tanggal_lahir=dob,
                pekerjaan=kerja, alamat=alamat, no_telp=telp, nama_wali=wali, hubungan_wali=hub, alergi=alergi,
            )
            for nik, nama, jk, tempat, dob, kerja, alamat, telp, wali, hub, alergi in PATIENTS
        ]
        budi, siti, aisyah, hartono, maria, joko = patients
        today = timezone.localdate()

        def past_visit(patient, days_ago, doctor, s, o, a, p, dx, px=()):
            day = today - timedelta(days=days_ago)
            visit = Visit.objects.create(
                patient=patient, doctor=doctor, tanggal=day, status=Visit.Status.SUDAH,
                subjective=s, objective=o, assessment=a, plan=p,
                registered_at=timezone.now() - timedelta(days=days_ago, hours=2),
                examined_at=timezone.now() - timedelta(days=days_ago, hours=1), examined_by=doctor,
            )
            for i, (code, name) in enumerate(dx):
                Diagnosis.objects.create(visit=visit, code=code, name=name, urutan=i)
            for i, (code, name) in enumerate(px):
                Procedure.objects.create(visit=visit, code=code, name=name, urutan=i)

        past_visit(
            hartono, 62, doctors[0],
            'Kontrol rutin hipertensi. Pusing sesekali di pagi hari. Obat diminum teratur.',
            'TD 158/94 mmHg, N 84x/m, RR 18x/m, S 36,6 C. BB 72 kg. Jantung/paru dbn.',
            'Hipertensi esensial belum terkontrol',
            'Amlodipin 1x10 mg (30 hari)\nDiet rendah garam\nKontrol 1 bulan',
            [('I10', 'Essential (primary) hypertension')],
            [('89.52', 'Electrocardiogram')],
        )
        past_visit(
            hartono, 30, doctors[0],
            'Kontrol hipertensi dan gula darah. Sering haus dan BAK malam hari.',
            'TD 142/88 mmHg, N 80x/m. GDS 246 mg/dL. Kaki: sensibilitas baik, tidak ada luka.',
            'Hipertensi esensial; DM tipe 2 baru terdiagnosis',
            'Amlodipin 1x10 mg\nMetformin 2x500 mg sesudah makan\nCek HbA1c, profil lipid, kreatinin\nEdukasi diet DM',
            [('I10', 'Essential (primary) hypertension'), ('E11.9', 'Type 2 diabetes mellitus without complications')],
            [('90.59', 'Microscopic examination of blood, other microscopic examination'), ('94.49', 'Other counseling')],
        )
        past_visit(
            budi, 14, doctors[1],
            'Batuk pilek 3 hari, nyeri telan, demam ringan. Riwayat alergi amoksisilin (gatal-gatal).',
            'S 37,8 C, faring hiperemis, tonsil T1-T1, paru vesikuler tanpa ronkhi.',
            'ISPA (faringitis akut)',
            'Paracetamol 3x500 mg prn demam\nGG + CTM 3x1\nMinum air hangat, istirahat',
            [('J02.9', 'Acute pharyngitis, unspecified')],
        )
        past_visit(
            aisyah, 7, doctors[0],
            'Diare 4x sejak kemarin, cair, tidak ada darah. Masih mau minum. Muntah 1x.',
            'BB 13 kg, S 37,4 C, mata tidak cekung, turgor kembali cepat, bising usus meningkat.',
            'Diare akut tanpa dehidrasi',
            'Oralit 100 cc setiap BAB cair\nZinc 1x20 mg selama 10 hari\nLanjutkan makan, ASI\nKembali bila tanda dehidrasi',
            [('A09', 'Other gastroenteritis and colitis of infectious and unspecified origin')],
        )

        for patient, doctor in ((hartono, doctors[0]), (siti, doctors[0]), (maria, doctors[1]), (joko, doctors[0])):
            Visit.objects.create(patient=patient, doctor=doctor, tanggal=today)

        self.stdout.write(self.style.SUCCESS(
            f'Demo data created. Users: {", ".join(u[0] for u in USERS)} (password in core/management/commands/seed_demo.py).'
        ))
