import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from PIL import Image

from django.core.exceptions import ValidationError

from .forms import UserForm
from .models import AuditLog, Counter, Diagnosis, Icd9, Icd10, Patient, Penunjang, Procedure, User, Visit
from .permissions import PERMS, ROLES
from .utils import card_code, card_qr, format_age, parse_card_code
from .validators import CharacterClassValidator

TEMP_MEDIA = tempfile.mkdtemp()


def make_user(username, *roles):
    user = User.objects.create_user(username=username, password='pw-test-123', display_name=username.title())
    user.groups.set(Group.objects.filter(name__in=roles))
    return user


def make_patient(**kwargs):
    data = dict(nama='Budi Santoso', jenis_kelamin='L', tanggal_lahir=date(1996, 1, 1), alamat='Jl. ABC, Solo')
    data.update(kwargs)
    return Patient.objects.create(**data)


def jpeg_upload(name='foto.jpg', size=(1200, 1600)):
    buf = BytesIO()
    Image.new('RGB', size, (200, 220, 240)).save(buf, 'JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


class AgeTests(TestCase):
    def test_formats(self):
        today = date(2026, 10, 30)
        self.assertEqual(format_age(date(1996, 1, 1), today), '30 th')
        self.assertEqual(format_age(date(1996, 10, 31), today), '29 th')
        self.assertEqual(format_age(date(2024, 7, 15), today), '2 th 3 bl')
        self.assertEqual(format_age(date(2026, 5, 30), today), '5 bl')
        self.assertEqual(format_age(date(2026, 10, 20), today), '10 hr')


class PatientTests(TestCase):
    def test_rm_is_sequential_and_padded(self):
        a, b = make_patient(), make_patient(nama='Siti')
        self.assertEqual(a.no_rm, '000001')
        self.assertEqual(b.no_rm, '000002')

    def test_rm_skips_numbers_entered_by_hand(self):
        make_patient(no_rm='000001')
        self.assertEqual(make_patient(nama='Siti').no_rm, '000002')

    def test_blank_nik_is_stored_as_null(self):
        a, b = make_patient(nik=''), make_patient(nama='Siti', nik='')
        self.assertIsNone(a.nik)
        self.assertIsNone(b.nik)


class RegistrationTests(TestCase):
    def setUp(self):
        self.front = make_user('front', 'pendaftaran')
        self.doctor = make_user('dok', 'dokter')
        self.client.force_login(self.front)

    def test_create_patient_and_register(self):
        res = self.client.post(reverse('api_patient_create'), {
            'nama': '  siti   rahma ', 'jenis_kelamin': 'P', 'tanggal_lahir': '1990-05-01',
            'alamat': 'Solo', 'hubungan_wali': 'ibu',
        })
        self.assertEqual(res.status_code, 200, res.content)
        patient_id = res.json()['patient']['id']
        self.assertEqual(Patient.objects.get(pk=patient_id).nama, 'siti rahma')

        res = self.client.post(reverse('api_register_visit'), {'patient': patient_id, 'doctor': self.doctor.id})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['no_antrian'], 1)

        other = make_patient()
        res = self.client.post(reverse('api_register_visit'), {'patient': other.id, 'doctor': self.doctor.id})
        self.assertEqual(res.json()['no_antrian'], 2)

    def test_patient_form_errors_are_returned(self):
        res = self.client.post(reverse('api_patient_create'), {'nama': '', 'nik': '123'})
        self.assertEqual(res.status_code, 400)
        errors = res.json()['errors']
        self.assertIn('nama', errors)
        self.assertIn('nik', errors)
        self.assertIn('tanggal_lahir', errors)

    def test_same_doctor_twice_is_blocked(self):
        patient = make_patient()
        self.client.post(reverse('api_register_visit'), {'patient': patient.id, 'doctor': self.doctor.id})
        res = self.client.post(reverse('api_register_visit'), {'patient': patient.id, 'doctor': self.doctor.id})
        self.assertEqual(res.status_code, 400)
        self.assertIn('sudah dalam antrian', res.json()['error'])

    def test_non_doctor_cannot_be_chosen(self):
        patient = make_patient()
        res = self.client.post(reverse('api_register_visit'), {'patient': patient.id, 'doctor': self.front.id})
        self.assertEqual(res.status_code, 400)

    def test_nik_validation(self):
        make_patient(nik='3372011201960001')
        patient = make_patient(nama='Siti')
        url = reverse('api_patient_nik', args=[patient.id])
        self.assertEqual(self.client.post(url, {'nik': '12345'}).status_code, 400)
        res = self.client.post(url, {'nik': '3372011201960001'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('Budi Santoso', res.json()['error'])
        self.assertEqual(self.client.post(url, {'nik': '3372011201960002'}).status_code, 200)

    def test_search(self):
        make_patient(nama='Budi Santoso', alamat='Jl. Slamet Riyadi, Solo')
        make_patient(nama='Budi Hartono', alamat='Klaten')
        res = self.client.get(reverse('api_patient_search'), {'q': 'budi solo'})
        names = [r['nama'] for r in res.json()['results']]
        self.assertEqual(names, ['Budi Santoso'])

    def test_rm_list_partial_has_only_results(self):
        make_patient(nama='Budi Santoso')
        make_patient(nama='Siti Rahma')
        res = self.client.get(reverse('rm_list'), {'q': 'siti', 'sort': 'nama', 'partial': 1})
        self.assertContains(res, 'Siti Rahma')
        self.assertNotContains(res, 'Budi Santoso')
        self.assertContains(res, '1 pasien cocok dengan “siti”')  # refreshes the count under the title
        self.assertNotContains(res, '<form')  # the search box is left alone while typing
        self.assertNotContains(res, 'partial=')  # sort links lead to the full page

    def test_cancel(self):
        visit = Visit.objects.create(patient=make_patient(), doctor=self.doctor)
        self.assertEqual(self.client.post(reverse('visit_cancel', args=[visit.id])).status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.status, Visit.Status.BATAL)

    def test_list_page_renders(self):
        Visit.objects.create(patient=make_patient(), doctor=self.doctor)
        res = self.client.get(reverse('pendaftaran'))
        self.assertContains(res, 'Budi Santoso')
        self.assertContains(res, 'Form daftar pasien')
        self.assertContains(res, 'id="foto-dialog"')  # photos can be taken while registering
        self.assertNotContains(res, '{#')  # a multi-line {# #} comment would leak into the page

    def test_edit_found_patient_before_registering(self):
        patient = make_patient(nama='Budi Santos', hubungan_wali='ayah', nama_wali='Slamet')
        found = self.client.get(reverse('api_patient_search'), {'q': 'budi'}).json()['results'][0]
        fields = found['fields']
        self.assertEqual(fields['tanggal_lahir'], '1996-01-01')
        self.assertEqual(fields['hubungan_wali'], 'ayah')

        fields.update(nama='Budi Santoso', alamat='Jl. Slamet Riyadi, Solo')
        url = reverse('api_patient_update', args=[patient.id])
        res = self.client.post(url, {k: v or '' for k, v in fields.items()})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['patient']['nama'], 'Budi Santoso')
        patient.refresh_from_db()
        self.assertEqual((patient.alamat, patient.nama_wali), ('Jl. Slamet Riyadi, Solo', 'Slamet'))
        log = AuditLog.objects.get(action='ubah_pasien', patient=patient)
        self.assertEqual(json.loads(log.detail)['fields'], ['nama', 'alamat'])

        res = self.client.post(url, {**fields, 'nama': '', 'nik': '123'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.json()['errors']), {'nama', 'nik'})

    def test_edit_button_follows_patient_edit_permission(self):
        self.assertContains(self.client.get(reverse('pendaftaran')), 'data-update-url')
        with mock.patch.dict(PERMS, {'patient_edit': {'admin'}}):
            self.assertNotContains(self.client.get(reverse('pendaftaran')), 'data-update-url')
            res = self.client.post(reverse('api_patient_update', args=[make_patient().id]), {'nama': 'X'})
            self.assertEqual(res.status_code, 403)


@override_settings(
    KARTU_NAMA='dr. Andi Pratama, M.Kes',
    KARTU_ALAMAT='Jl. Melati No. 1 Solo, 57100 - Telp. (0271) 000 000',
    KARTU_WA='0812 0000 0000',
)
class PatientCardTests(TestCase):
    def setUp(self):
        self.front = make_user('front', 'pendaftaran')
        self.doctor = make_user('dok', 'dokter')
        self.client.force_login(self.front)
        self.patient = make_patient(nama='Siti Rahma', alamat='Desa Mojosongo RT 02/05, Boyolali')

    def test_card_code(self):
        self.assertEqual(card_code('000123'), 'RM:000123')
        self.assertEqual(parse_card_code('RM:000123'), '000123')
        self.assertEqual(parse_card_code(' rm:000123\n'), '000123')  # Caps Lock on the USB scanner, trailing Enter
        for text in ('000123', 'RM:', 'https://example.org', '', None):
            self.assertIsNone(parse_card_code(text))
        # Longer RM numbers make a larger code rather than a weaker one.
        self.assertEqual(card_qr('S1001').designator, '1-H')
        self.assertEqual(card_qr('12345678').designator, '2-H')

    def test_card_page_has_patient_data_and_qr(self):
        res = self.client.get(reverse('kartu_cetak', args=[self.patient.id]))
        self.assertEqual(res.status_code, 200)
        for text in ('dr. Andi Pratama, M.Kes', 'Jl. Melati No. 1 Solo, 57100 - Telp. (0271) 000 000', '0812 0000 0000',
                     'KARTU PERIKSA', self.patient.no_rm, 'Siti Rahma', '01-01-1996',
                     'Desa Mojosongo RT 02/05, Boyolali', 'Kartu ini harap dibawa setiap kali periksa'):
            self.assertContains(res, text)
        qr = card_qr(self.patient.no_rm)
        self.assertEqual(qr.designator, '1-H')  # 21 x 21 modules, highest error correction
        self.assertContains(res, qr.svg_inline(scale=1, border=0, omitsize=True, dark='#000'), html=False)
        self.assertNotContains(res, 'data-autoprint')
        self.assertTrue(AuditLog.objects.filter(action='cetak_kartu', patient=self.patient).exists())

        res = self.client.get(reverse('kartu_cetak', args=[self.patient.id]), {'cetak': 1})
        self.assertContains(res, 'data-autoprint')

    def test_card_page_without_optional_header_lines(self):
        self.patient.tanggal_lahir = None  # imported record without a date of birth
        self.patient.save()
        with override_settings(KARTU_NAMA='Klinik Sehat', KARTU_ALAMAT='', KARTU_WA=''):
            res = self.client.get(reverse('kartu_cetak', args=[self.patient.id]))
        self.assertContains(res, 'Klinik Sehat')
        self.assertNotContains(res, 'aria-label="WhatsApp"')
        self.assertNotContains(res, 'class="k-alamat"')

    def test_scanned_card_finds_patient(self):
        Visit.objects.create(patient=self.patient, doctor=self.doctor)
        res = self.client.get(reverse('api_patient_by_card'), {'kode': f'RM:{self.patient.no_rm}'})
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()['patient']
        self.assertEqual(data['id'], self.patient.id)
        self.assertEqual(data['kartu_url'], reverse('kartu_cetak', args=[self.patient.id]))
        self.assertEqual([v['no_antrian'] for v in data['today']], [1])  # the dialog warns about a second registration

        imported = make_patient(no_rm='S1001', nama='Slamet')
        res = self.client.get(reverse('api_patient_by_card'), {'kode': 'rm:s1001'})
        self.assertEqual(res.json()['patient']['id'], imported.id)

    def test_unknown_or_foreign_codes(self):
        res = self.client.get(reverse('api_patient_by_card'), {'kode': 'RM:999999'})
        self.assertEqual(res.status_code, 404)
        self.assertIn('999999', res.json()['error'])
        res = self.client.get(reverse('api_patient_by_card'), {'kode': 'https://example.org/'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['error'], 'QR ini bukan kartu periksa.')

    def test_pages_offer_scan_and_print(self):
        res = self.client.get(reverse('pendaftaran'))
        self.assertContains(res, 'data-card-prefix="RM:"')
        self.assertContains(res, 'data-step="scan"')
        self.assertContains(res, 'vendor/jsQR.js')
        self.assertContains(res, 'core/scan.js')
        res = self.client.get(reverse('rm_detail', args=[self.patient.id]))
        self.assertContains(res, f'{reverse("kartu_cetak", args=[self.patient.id])}?cetak=1')

    def test_card_follows_rekam_medis_permission(self):
        with mock.patch.dict(PERMS, {'rekam_medis': {'admin'}}):
            self.assertEqual(self.client.get(reverse('kartu_cetak', args=[self.patient.id])).status_code, 403)
            res = self.client.get(reverse('api_patient_by_card'), {'kode': f'RM:{self.patient.no_rm}'})
            self.assertEqual(res.status_code, 403)
            res = self.client.get(reverse('pendaftaran'))
            self.assertNotContains(res, 'data-card-url')
            self.assertNotContains(res, 'data-step="scan"')


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PatientPhotoTests(TestCase):
    def setUp(self):
        self.user = make_user('front', 'pendaftaran')
        self.patient = make_patient()
        self.client.force_login(self.user)

    def upload(self, file):
        return self.client.post(reverse('api_patient_foto', args=[self.patient.id]), {'foto': file})

    def test_upload_replace_delete(self):
        res = self.upload(jpeg_upload(size=(1600, 1200)))
        self.assertEqual(res.status_code, 200, res.content)
        self.patient.refresh_from_db()
        first = self.patient.foto.path
        with Image.open(first) as img:
            self.assertEqual(img.size, (800, 600))
        served = self.client.get(res.json()['foto_url'])
        self.assertEqual(served['Content-Type'], 'image/jpeg')
        self.assertTrue(served.getvalue().startswith(b'\xff\xd8'))  # also closes the file
        self.assertContains(self.client.get(reverse('rm_detail', args=[self.patient.id])), res.json()['foto_url'])
        found = self.client.get(reverse('api_patient_search'), {'q': 'budi'}).json()['results'][0]
        self.assertEqual(found['foto_url'], res.json()['foto_url'])  # shown on the registration doctor step

        second = self.upload(jpeg_upload()).json()['foto_url']
        self.assertNotEqual(second, res.json()['foto_url'])
        self.assertFalse(os.path.exists(first))  # the replaced photo is removed
        self.assertEqual(AuditLog.objects.filter(action='foto_pasien', patient=self.patient).count(), 2)

        res = self.client.post(reverse('api_patient_foto_delete', args=[self.patient.id]))
        self.assertEqual(res.json()['foto_url'], '')
        self.patient.refresh_from_db()
        self.assertFalse(self.patient.foto)
        self.assertEqual(self.client.get(reverse('patient_foto', args=[self.patient.id])).status_code, 404)

    def test_rejects_pdf_and_unreadable_files(self):
        pdf = SimpleUploadedFile('scan.pdf', b'%PDF-1.4 fake', content_type='application/pdf')
        self.assertEqual(self.upload(pdf).status_code, 400)
        junk = SimpleUploadedFile('foto.jpg', b'not an image', content_type='image/jpeg')
        res = self.upload(junk)
        self.assertEqual(res.status_code, 400)
        self.assertIn('tidak dapat dibaca', res.json()['error'])

    def test_requires_a_role(self):
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.upload(jpeg_upload()).status_code, 403)
        self.assertEqual(self.client.get(reverse('patient_foto', args=[self.patient.id])).status_code, 403)


class ExaminationTests(TestCase):
    def setUp(self):
        self.doctor = make_user('dok', 'dokter')
        self.nurse = make_user('ns', 'perawat')
        self.front = make_user('front', 'pendaftaran')
        self.patient = make_patient()
        self.visit = Visit.objects.create(patient=self.patient, doctor=self.doctor)
        self.url = reverse('pemeriksaan_detail', args=[self.visit.id])
        self.soap = {
            'subjective': 'Batuk 3 hari', 'objective': 'S 37,8', 'assessment': 'ISPA', 'plan': 'Paracetamol',
            'diagnoses': json.dumps([{'code': 'j06.9', 'name': 'ISPA'}, {'code': 'J06.9', 'name': 'ISPA'}]),
        }

    def test_doctor_saves_soap(self):
        self.client.force_login(self.doctor)
        res = self.client.post(self.url, self.soap)
        self.assertEqual(res.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.Status.SUDAH)
        self.assertEqual([d.code for d in self.visit.diagnoses.all()], ['J06.9'])
        self.assertTrue(AuditLog.objects.filter(action='simpan_soap').exists())

    def test_required_fields(self):
        self.client.force_login(self.doctor)
        res = self.client.post(self.url, {**self.soap, 'plan': '   '})
        self.assertEqual(res.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.Status.BELUM)

    def test_nurse_reads_but_cannot_write(self):
        self.client.force_login(self.nurse)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.soap).status_code, 403)

    def test_front_desk_has_no_access(self):
        self.client.force_login(self.front)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse('pemeriksaan')).status_code, 403)

    def test_history_shows_previous_visits(self):
        Visit.objects.create(
            patient=self.patient, doctor=self.doctor, tanggal=date(2026, 1, 5), status=Visit.Status.SUDAH,
            subjective='Kontrol tensi lama', objective='TD 150/90', assessment='HT', plan='Amlodipin',
            examined_at=timezone.now(),
        )
        self.client.force_login(self.doctor)
        res = self.client.get(self.url)
        self.assertContains(res, 'Kontrol tensi lama')
        self.assertContains(res, 'A + ICD-X')

    def test_history_shows_who_wrote_the_note(self):
        covering = make_user('cover', 'dokter')
        Visit.objects.create(
            patient=self.patient, doctor=self.doctor, tanggal=date(2026, 1, 5), status=Visit.Status.SUDAH,
            subjective='Ditulis dokter pengganti', objective='-', assessment='-', plan='-',
            examined_at=timezone.now(), examined_by=covering,
        )
        self.client.force_login(self.doctor)
        res = self.client.get(reverse('rm_detail', args=[self.patient.id]))
        self.assertContains(res, 'Penulis')
        self.assertContains(res, '<div class="author-name">Cover</div>', html=True)
        self.assertContains(res, 'terdaftar: Dok')

    def test_icd_search(self):
        Icd10.objects.create(code='J06.9', name='Acute upper respiratory infection, unspecified', alias='ISPA')
        Icd10.objects.create(code='I10', name='Essential (primary) hypertension', alias='Hipertensi esensial')
        self.client.force_login(self.doctor)
        search = lambda q: self.client.get(reverse('api_icd10'), {'q': q}).json()['results']
        self.assertEqual([r['code'] for r in search('J069')], ['J06.9'])
        self.assertEqual([r['code'] for r in search('hypertension')], ['I10'])
        # Indonesian words still find the code, but only the English title is returned.
        self.assertEqual(search('hipertensi'), [{'code': 'I10', 'name': 'Essential (primary) hypertension'}])

    def test_saved_diagnoses_switch_to_english(self):
        import importlib

        from django.apps import apps as django_apps

        from .models import Diagnosis
        visit = Visit.objects.create(patient=self.patient, doctor=self.doctor, status=Visit.Status.SUDAH)
        old = Diagnosis.objects.create(visit=visit, code='I10', name='Hipertensi esensial (primer)')
        earlier = Diagnosis.objects.create(visit=visit, code='E78.5', name='Hiperlipidemia')
        typed = Diagnosis.objects.create(visit=visit, code='I10', name='HT grade 2')
        free = Diagnosis.objects.create(visit=visit, code='', name='Hipertensi esensial (primer)')

        importlib.import_module('core.migrations.0006_diagnosis_english').to_english(django_apps, None)

        for d in (old, earlier, typed, free):
            d.refresh_from_db()
        self.assertEqual(old.name, 'Essential (primary) hypertension')
        self.assertEqual(earlier.name, 'Hyperlipidaemia, unspecified')
        self.assertEqual(typed.name, 'HT grade 2')                      # not a bundled name: kept
        self.assertEqual(free.name, 'Hipertensi esensial (primer)')     # free text has no code: kept

    def test_bundled_icd_list_is_english(self):
        from django.core.management import call_command
        call_command('load_icd10', verbosity=0)
        self.assertEqual(Icd10.objects.get(code='I10').name, 'Essential (primary) hypertension')
        self.assertEqual(Icd10.objects.get(code='A09').alias, 'Diare dan gastroenteritis diduga infeksi')

    def test_doctor_saves_procedures(self):
        self.client.force_login(self.doctor)
        procedures = [{'code': '86.59', 'name': 'Closure of skin'}, {'code': '86.59', 'name': 'Closure of skin'},
                      {'code': '', 'name': '  Ganti   verban '}]
        self.client.post(self.url, {**self.soap, 'procedures': json.dumps(procedures)})
        self.assertEqual([(p.code, p.name) for p in self.visit.procedures.all()],
                         [('86.59', 'Closure of skin'), ('', 'Ganti verban')])
        detail = json.loads(AuditLog.objects.get(action='simpan_soap').detail)
        self.assertEqual([p['code'] for p in detail['ICD9']], ['86.59', ''])
        # Saving again replaces the list.
        self.client.post(self.url, {**self.soap, 'procedures': '[]'})
        self.assertFalse(self.visit.procedures.exists())

    def test_procedure_history(self):
        def past(day, **kwargs):
            return Visit.objects.create(
                patient=self.patient, doctor=self.doctor, tanggal=day, status=Visit.Status.SUDAH,
                subjective='-', objective='-', assessment='-', plan='-', examined_at=timezone.now(), **kwargs,
            )
        with_px = past(date(2026, 1, 5))
        Procedure.objects.create(visit=with_px, code='96.52', name='Irrigation of ear')
        past(date(2026, 2, 1))  # no procedure: not listed under Tindakan

        self.client.force_login(self.doctor)
        res = self.client.get(self.url)
        self.assertEqual([v.id for v in res.context['riwayat_tindakan']], [with_px.id])
        self.assertContains(res, 'Irrigation of ear')
        self.assertContains(res, 'data-copy="px"')
        # The patient record shows it read-only; the front desk does not see it.
        res = self.client.get(reverse('rm_detail', args=[self.patient.id]))
        self.assertContains(res, 'Irrigation of ear')
        self.assertNotContains(res, 'data-copy="px"')
        self.client.force_login(self.front)
        self.assertNotContains(self.client.get(reverse('rm_detail', args=[self.patient.id])), 'Irrigation of ear')

    def test_icd9_search_and_bundled_list(self):
        call_command('load_icd9', verbosity=0)
        self.assertEqual(Icd9.objects.get(code='89.52').name, 'Electrocardiogram')
        self.client.force_login(self.doctor)
        search = lambda q: [r['code'] for r in self.client.get(reverse('api_icd9'), {'q': q}).json()['results']]
        self.assertEqual(search('8659'), ['86.59'])
        self.assertEqual(search('86.59'), ['86.59'])
        # Indonesian search words find the English titles.
        self.assertIn('86.59', search('jahit luka'))
        self.assertIn('93.94', search('nebu'))
        self.client.force_login(self.front)
        self.assertEqual(self.client.get(reverse('api_icd9'), {'q': 'nebu'}).status_code, 403)

    def test_allergy_update(self):
        self.client.force_login(self.nurse)
        res = self.client.post(reverse('api_patient_alergi', args=[self.patient.id]), {'alergi': 'Amoksisilin'})
        self.assertTrue(res.json()['has_allergy'])
        self.client.force_login(self.front)
        res = self.client.post(reverse('api_patient_alergi', args=[self.patient.id]), {'alergi': ''})
        self.assertEqual(res.status_code, 403)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PenunjangTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = make_user('ns', 'perawat')
        self.patient = make_patient()
        self.client.force_login(self.user)

    def upload(self, **extra):
        return self.client.post(reverse('penunjang_upload', args=[self.patient.id]), {
            'file': jpeg_upload(), 'kategori': 'lab', **extra,
        })

    def test_upload_list_reorder_delete(self):
        first = self.upload().json()['item']
        second = self.upload().json()['item']
        self.assertEqual((first['page'], second['page']), (1, 2))

        items = self.client.get(reverse('penunjang_list', args=[self.patient.id])).json()['items']
        self.assertEqual([i['id'] for i in items], [second['id'], first['id']])  # newest first

        res = self.client.post(reverse('penunjang_reorder', args=[self.patient.id]),
                               json.dumps({'order': [first['id'], second['id']]}), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        items = self.client.get(reverse('penunjang_list', args=[self.patient.id])).json()['items']
        self.assertEqual([(i['id'], i['page']) for i in items], [(first['id'], 2), (second['id'], 1)])

        self.client.post(reverse('penunjang_delete', args=[first['id']]))
        items = self.client.get(reverse('penunjang_list', args=[self.patient.id])).json()['items']
        self.assertEqual([i['id'] for i in items], [second['id']])
        self.assertIsNotNone(Penunjang.objects.get(pk=first['id']).deleted_at)  # soft delete

    def test_image_is_downscaled_and_thumbnail_served(self):
        item = self.client.post(reverse('penunjang_upload', args=[self.patient.id]), {
            'file': jpeg_upload(size=(3000, 4000)), 'kategori': 'ekg',
        }).json()['item']
        stored = Penunjang.objects.get(pk=item['id'])
        with Image.open(stored.file.path) as img:
            self.assertEqual(max(img.size), 2000)
        res = self.client.get(item['thumb_url'])
        self.assertEqual(res['Content-Type'], 'image/jpeg')

    def test_rotate(self):
        item = self.upload().json()['item']
        res = self.client.post(reverse('penunjang_update', args=[item['id']]), {'rotate': 90, 'note': 'Lab RS'})
        self.assertEqual(res.status_code, 200)
        stored = Penunjang.objects.get(pk=item['id'])
        with Image.open(stored.file.path) as img:
            self.assertEqual(img.size, (1600, 1200))
        self.assertEqual(stored.note, 'Lab RS')

    def test_rejects_non_image(self):
        res = self.client.post(reverse('penunjang_upload', args=[self.patient.id]), {
            'file': SimpleUploadedFile('x.jpg', b'not an image'), 'kategori': 'lab',
        })
        self.assertEqual(res.status_code, 400)

    def test_pdf_upload(self):
        pdf = SimpleUploadedFile('hasil.pdf', b'%PDF-1.4\n%%EOF\n', content_type='application/pdf')
        res = self.client.post(reverse('penunjang_upload', args=[self.patient.id]), {'file': pdf, 'kategori': 'lab'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['item']['is_pdf'])

    def test_attaches_to_todays_visit(self):
        doctor = make_user('dok', 'dokter')
        visit = Visit.objects.create(patient=self.patient, doctor=doctor)
        item = self.upload().json()['item']
        self.assertEqual(Penunjang.objects.get(pk=item['id']).visit, visit)

    def test_mobile_pages(self):
        self.assertEqual(self.client.get(reverse('mobile_home')).status_code, 200)
        res = self.client.get(reverse('mobile_upload', args=[self.patient.id]))
        self.assertContains(res, 'Ambil foto')
        self.assertEqual(self.client.get(reverse('penunjang_qr', args=[self.patient.id]))['Content-Type'], 'image/svg+xml')


class AccessTests(TestCase):
    def test_login_required(self):
        res = self.client.get(reverse('rm_list'))
        self.assertEqual(res.status_code, 302)
        self.assertIn(reverse('login'), res['Location'])

    def test_home_redirects_by_role(self):
        self.client.force_login(make_user('dok', 'dokter'))
        self.assertRedirects(self.client.get('/'), reverse('pemeriksaan'))
        self.client.force_login(make_user('front', 'pendaftaran'))
        self.assertRedirects(self.client.get('/'), reverse('pendaftaran'))

    def test_front_desk_does_not_see_soap_history(self):
        doctor = make_user('dok', 'dokter')
        patient = make_patient()
        Visit.objects.create(patient=patient, doctor=doctor, status=Visit.Status.SUDAH, subjective='Rahasia klinis')
        self.client.force_login(make_user('front', 'pendaftaran'))
        res = self.client.get(reverse('rm_detail', args=[patient.id]))
        self.assertNotContains(res, 'Rahasia klinis')
        self.assertContains(res, 'Kunjungan')

    def test_front_desk_has_no_penunjang(self):
        patient = make_patient()
        self.client.force_login(make_user('front', 'pendaftaran'))
        res = self.client.get(reverse('rm_detail', args=[patient.id]))
        self.assertNotContains(res, 'data-tab="penunjang"')
        self.assertNotContains(res, 'data-penunjang')
        for url in (reverse('penunjang_list', args=[patient.id]), reverse('penunjang_qr', args=[patient.id]),
                    reverse('mobile_home'), reverse('mobile_upload', args=[patient.id])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        upload = self.client.post(reverse('penunjang_upload', args=[patient.id]), {'file': jpeg_upload()})
        self.assertEqual(upload.status_code, 403)

    def test_phone_login_without_upload_access_goes_home(self):
        make_user('front', 'pendaftaran')
        res = self.client.post(reverse('login'), {'username': 'front', 'password': 'pw-test-123'},
                               HTTP_USER_AGENT='Mozilla/5.0 (Linux; Android 14) Mobile Safari')
        self.assertRedirects(res, reverse('home'), fetch_redirect_response=False)

    def test_login_lockout(self):
        make_user('dok', 'dokter')
        for _ in range(10):
            self.client.post(reverse('login'), {'username': 'dok', 'password': 'wrong'})
        res = self.client.post(reverse('login'), {'username': 'dok', 'password': 'pw-test-123'})
        self.assertContains(res, 'Terlalu banyak percobaan')

    def test_login_lockout_covers_django_admin(self):
        User.objects.create_superuser(username='boss', password='pw-test-123', display_name='Boss')
        url = reverse('admin:login')
        for _ in range(10):
            self.client.post(url, {'username': 'boss', 'password': 'wrong'})
        for login_url in (url, reverse('login')):
            res = self.client.post(login_url, {'username': 'boss', 'password': 'pw-test-123'})
            self.assertContains(res, 'Terlalu banyak percobaan')
        AuditLog.objects.filter(action='login_gagal').delete()
        res = self.client.post(url, {'username': 'boss', 'password': 'pw-test-123', 'next': reverse('admin:index')})
        self.assertRedirects(res, reverse('admin:index'))

    def test_admin_cannot_lock_themself_out(self):
        admin = make_user('boss', 'admin')
        self.client.force_login(admin)
        res = self.client.post(reverse('user_edit', args=[admin.id]), {
            'username': 'boss', 'display_name': 'Boss', 'roles': ['dokter'], 'is_active': 'on',
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'peran Admin')

    def test_admin_creates_user(self):
        self.client.force_login(make_user('boss', 'admin'))
        res = self.client.post(reverse('user_create'), {
            'username': 'drbaru', 'display_name': 'dr. Baru', 'roles': ['dokter'], 'is_active': 'on',
            'password1': 'Klinik-Sehat-2026', 'password2': 'Klinik-Sehat-2026',
        })
        self.assertRedirects(res, reverse('user_list'))
        user = User.objects.get(username='drbaru')
        self.assertEqual(user.roles, {'dokter'})
        self.assertTrue(user.check_password('Klinik-Sehat-2026'))


class CallTests(TestCase):
    def setUp(self):
        self.doctor = make_user('dok', 'dokter')
        self.nurse = make_user('ns', 'perawat')
        self.front = make_user('front', 'pendaftaran')
        self.visit = Visit.objects.create(patient=make_patient(), doctor=self.doctor)
        self.version_url = reverse('queue_version')

    def version(self):
        return self.client.get(self.version_url).json()['v']

    def test_doctor_opening_patient_calls_them(self):
        self.client.force_login(self.front)
        before = self.version()
        self.client.force_login(self.doctor)
        self.client.get(reverse('pemeriksaan_detail', args=[self.visit.id]))
        self.visit.refresh_from_db()
        self.assertTrue(self.visit.is_called)
        self.assertEqual(self.visit.status_label, 'Dipanggil')

        self.client.force_login(self.front)
        self.assertNotEqual(self.version(), before)
        self.assertContains(self.client.get(reverse('pendaftaran')), 'Dipanggil')

    def test_nurse_viewing_does_not_call(self):
        self.client.force_login(self.nurse)
        self.client.get(reverse('pemeriksaan_detail', args=[self.visit.id]))
        self.visit.refresh_from_db()
        self.assertFalse(self.visit.is_called)

    def test_undo_call(self):
        self.client.force_login(self.doctor)
        self.client.get(reverse('pemeriksaan_detail', args=[self.visit.id]))
        self.client.post(reverse('visit_uncall', args=[self.visit.id]))
        self.visit.refresh_from_db()
        self.assertFalse(self.visit.is_called)
        self.assertEqual(self.visit.status_label, 'Belum diperiksa')

    def test_version_requires_queue_access(self):
        outsider = User.objects.create_user(username='x', password='pw-test-123')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.version_url).status_code, 403)


class LaporanTests(TestCase):
    def setUp(self):
        self.doctor = make_user('dok', 'dokter')
        self.doctor.display_name = 'dr. Andi Pratama'
        self.doctor.save()
        self.other_doctor = make_user('dok2', 'dokter')
        self.url = reverse('laporan')
        self.params = {'bulan': '2026-09', 'dokter': self.doctor.id, 'puskesmas': 'Contoh'}

        self.dm = make_patient(nama='Contoh Satu', nik='9999011503810001', tanggal_lahir=date(1981, 3, 15),
                               alamat='Jl. Mawar 1')
        self.ht = make_patient(nama='Contoh Dua', jenis_kelamin='P', tanggal_lahir=date(1972, 6, 10), alamat='Jl. Mawar 2')
        self.both = make_patient(nama='Contoh Tiga', tanggal_lahir=date(1991, 1, 20), alamat='Jl. Mawar 3')
        self.later = make_patient(nama='Contoh Empat', jenis_kelamin='P', tanggal_lahir=date(1986, 8, 9), alamat='Jl. Mawar 4')
        self.no_dob = make_patient(nama='Contoh Lima', tanggal_lahir=None, alamat='Jl. Mawar 5')
        self.visit(self.dm, date(2026, 9, 5), 'E11.9')
        self.visit(self.ht, date(2026, 9, 10), 'I10')
        self.visit(self.both, date(2026, 8, 20), 'E11', doctor=self.other_doctor)  # earlier, other doctor: history
        self.visit(self.both, date(2026, 9, 12), 'I10', 'J06.9')
        self.visit(self.later, date(2026, 9, 15), 'I10')
        self.visit(self.later, date(2026, 10, 2), 'E11.9')  # after the month: not part of the history yet
        self.visit(self.no_dob, date(2026, 9, 16), 'I10')
        # Not listed: aged 60, other diagnosis, other month, other doctor, not examined.
        self.visit(make_patient(nama='Lansia', tanggal_lahir=date(1966, 9, 1)), date(2026, 9, 3), 'I10')
        self.visit(make_patient(nama='Flu'), date(2026, 9, 3), 'J06.9')
        self.visit(make_patient(nama='Oktober'), date(2026, 10, 1), 'I10')
        self.visit(make_patient(nama='Lain'), date(2026, 9, 3), 'I10', doctor=self.other_doctor)
        pending = Visit.objects.create(patient=make_patient(nama='Antri'), doctor=self.doctor, tanggal=date(2026, 9, 3))
        Diagnosis.objects.create(visit=pending, code='I10', name='I10')

    def visit(self, patient, tanggal, *codes, doctor=None):
        visit = Visit.objects.create(
            patient=patient, doctor=doctor or self.doctor, tanggal=tanggal, status=Visit.Status.SUDAH,
        )
        for i, code in enumerate(codes):
            Diagnosis.objects.create(visit=visit, code=code, name=code, urutan=i)
        return visit

    def test_preview_lists_dm_and_ht_patients(self):
        self.client.force_login(self.doctor)
        res = self.client.get(self.url, self.params)
        self.assertEqual(res.status_code, 200)
        rows = [(r.patient.nama, r.faktor_label) for r in res.context['rows']]
        self.assertEqual(rows, [
            ('Contoh Satu', 'DM'), ('Contoh Dua', 'HT'), ('Contoh Tiga', 'DM, HT'), ('Contoh Empat', 'HT'),
        ])
        self.assertEqual([p.nama for p in res.context['no_birth_date']], ['Contoh Lima'])
        self.assertContains(res, '9999 01 150381 0001')
        self.assertFalse(AuditLog.objects.filter(action='unduh_laporan').exists())

    def test_age_range_follows_the_form(self):
        self.client.force_login(self.doctor)
        res = self.client.get(self.url, {**self.params, 'usia_min': 15, 'usia_max': 120})
        self.assertIn('Lansia', [r.patient.nama for r in res.context['rows']])

    def test_excel_download(self):
        self.client.force_login(self.doctor)
        res = self.client.get(self.url, {**self.params, 'unduh': 'xlsx'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('laporan-dm-ht-2026-09.xlsx', res['Content-Disposition'])
        ws = load_workbook(BytesIO(res.content)).active
        self.assertEqual(ws['A1'].value, 'JEJARING FASYANKES SWASTA PUSKESMAS CONTOH')
        self.assertEqual((ws['A2'].value, ws['C2'].value), ('DPM', 'dr. Andi Pratama'))
        self.assertEqual((ws['A3'].value, ws['C3'].value), ('BULAN PELAPORAN', 'September Thn 2026'))
        self.assertEqual(ws['E4'].value, 'PENDUDUK USIA 15-59 TAHUN')
        self.assertEqual((ws['E6'].value, ws['F6'].value), ('L', 'P'))
        self.assertIn('E4:G4', [str(r) for r in ws.merged_cells.ranges])
        data = [[c.value for c in row] for row in ws.iter_rows(min_row=7, max_row=10)]
        self.assertEqual(data, [
            [1, 'CONTOH SATU', '9999 01 150381 0001', 'JL. MAWAR 1', 'L', None, 'DM'],
            [2, 'CONTOH DUA', '-', 'JL. MAWAR 2', None, 'P', 'HT'],
            [3, 'CONTOH TIGA', '-', 'JL. MAWAR 3', 'L', None, 'DM, HT'],
            [4, 'CONTOH EMPAT', '-', 'JL. MAWAR 4', None, 'P', 'HT'],
        ])
        self.assertIsNone(ws['A11'].value)
        self.assertEqual(ws['A12'].value, '*Jenis faktor resiko:')
        log = AuditLog.objects.get(action='unduh_laporan')
        self.assertEqual(json.loads(log.detail)['pasien'], 4)

    def test_defaults_and_invalid_month(self):
        self.client.force_login(self.doctor)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['form'].cleaned_data['dokter'], self.doctor)
        self.assertEqual(res.context['form'].cleaned_data['usia_min'], 15)
        with self.settings(PUSKESMAS_NAME='Contoh'):
            for params in ({}, {'puskesmas': ''}):
                res = self.client.get(self.url, params)
                self.assertEqual(res.context['form'].cleaned_data['puskesmas'], 'Contoh')
        res = self.client.get(self.url, {**self.params, 'bulan': '2026-13', 'unduh': 'xlsx'})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('Content-Disposition', res)
        self.assertTrue(res.context['form'].errors)

    def test_access(self):
        self.client.force_login(make_user('ns', 'perawat'))
        self.assertEqual(self.client.get(self.url, self.params).status_code, 200)
        self.client.force_login(make_user('front', 'pendaftaran'))
        self.assertEqual(self.client.get(self.url, self.params).status_code, 403)


@override_settings(EMR_HOSTS=['serverkliniksehat', '192.168.1.10'])
class HttpsRedirectTests(TestCase):
    def test_old_http_address_goes_to_https(self):
        res = self.client.get('/pendaftaran/?tanggal=2026-10-30', HTTP_HOST='serverkliniksehat:8765')
        self.assertEqual(res.status_code, 301)
        self.assertEqual(res['Location'], 'https://serverkliniksehat/pendaftaran/?tanggal=2026-10-30')
        res = self.client.get('/login/', HTTP_HOST='192.168.1.10:8765')
        self.assertEqual(res['Location'], 'https://192.168.1.10/login/')

    def test_https_health_check_and_other_hosts_are_served(self):
        self.assertEqual(self.client.get('/login/', HTTP_HOST='serverkliniksehat', secure=True).status_code, 200)
        self.assertEqual(self.client.get('/healthz', HTTP_HOST='127.0.0.1:8000').status_code, 200)
        self.assertEqual(self.client.get('/login/', HTTP_HOST='other-name:8765').status_code, 200)

    @override_settings(EMR_HOSTS=[])
    def test_off_without_emr_host(self):
        self.assertEqual(self.client.get('/login/', HTTP_HOST='serverkliniksehat:8765').status_code, 200)


class DefaultSecretTests(TestCase):
    def test_placeholder_secret_key_is_refused(self):
        env = {**os.environ, 'DJANGO_DEBUG': '', 'DJANGO_SECRET_KEY': 'change-me'}
        result = subprocess.run([sys.executable, '-c', 'import emr.settings'], cwd=settings.BASE_DIR, env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', result.stderr)

    def test_placeholder_admin_password_is_refused(self):
        with mock.patch.dict(os.environ, {'ADMIN_USERNAME': 'boss', 'ADMIN_PASSWORD': 'change-me'}):
            with self.assertRaisesMessage(CommandError, 'ADMIN_PASSWORD'):
                call_command('ensure_admin', stdout=StringIO())
            self.assertFalse(User.objects.exists())
            # An installation that already has an admin keeps starting.
            User.objects.create_superuser(username='boss', password='pw-test-123', display_name='Boss')
            call_command('ensure_admin', stdout=StringIO())


class PasswordRuleTests(TestCase):
    def test_character_classes(self):
        v = CharacterClassValidator(require_digit=True, require_upper=True, require_symbol=True)
        v.validate('Sehat-2026')
        with self.assertRaises(ValidationError) as ctx:
            v.validate('sehatsekali')
        self.assertIn('angka', ctx.exception.messages[0])
        self.assertIn('huruf besar', ctx.exception.messages[0])

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 4}},
    ])
    def test_rules_follow_settings(self):
        form = UserForm({'username': 'a', 'roles': ['dokter'], 'is_active': 'on',
                         'password1': '1234', 'password2': '1234'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn('4', form.fields['password1'].help_text)


class RoleTests(TestCase):
    def test_every_role_has_a_group_and_a_checkbox(self):
        self.assertEqual(set(Group.objects.filter(name__in=ROLES).values_list('name', flat=True)), set(ROLES))
        self.assertEqual([key for key, _ in UserForm().fields['roles'].choices], list(ROLES))


def write_dbf(path, fields, rows):
    """Minimal Visual FoxPro table, as MedPP writes them. fields: [(name, type, length)]; rows: dicts."""
    record_len = 1 + sum(length for _, _, length in fields)
    out = bytearray(struct.pack('<BBBBIHH', 0x30, 126, 10, 30, len(rows), 32 + 32 * len(fields) + 1, record_len))
    out += bytes(17) + b'\x03' + bytes(2)
    for name, ftype, length in fields:
        out += name.encode().ljust(11, b'\0') + ftype.encode() + bytes(4) + bytes([length, 0]) + bytes(14)
    out += b'\r'
    for row in rows:
        out += b'*' if row.get('deleted') else b' '
        for name, _, length in fields:
            out += str(row.get(name, '')).encode('cp1252').ljust(length)[:length]
    path.write_bytes(bytes(out) + b'\x1a')


PASIEN_DBF = [
    ('CNOMR', 'C', 30), ('CNMPASIEN', 'C', 30), ('CKELAMIN', 'C', 1), ('DTGLAHIR', 'D', 8), ('CAL1', 'C', 60),
    ('CAL2', 'C', 60), ('CKERJA', 'C', 50), ('CALERGI', 'C', 250), ('CNOTELP', 'C', 20),
]
RM_DBF = [
    ('CNOMR', 'C', 30), ('TGL', 'D', 8), ('ANAMNESA', 'C', 100), ('PERIKSA', 'C', 100), ('DIAGNOSA', 'C', 100),
    ('PENGOBATAN', 'C', 100), ('COL_ID', 'C', 100), ('UMUR', 'N', 3), ('CRUJUKKE', 'C', 50), ('CRUJUKDR', 'C', 50),
    ('CKDSAKIT', 'C', 20), ('CNMSAKIT', 'C', 200), ('CNMTERAPI', 'C', 200), ('DTGJANJI', 'D', 8),
    ('CKETJANJI', 'C', 200), ('CFILEGBR', 'C', 150), ('CKETGBR', 'C', 200),
]


class MedppImportTests(TestCase):
    def setUp(self):
        self.doctor = make_user('dok', 'dokter')
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        write_dbf(self.dir / 'PASIEN.DBF', PASIEN_DBF, [
            {'CNOMR': 'A1', 'CNMPASIEN': 'BUDI  SANTOSO', 'CKELAMIN': 'L', 'DTGLAHIR': '19800101',
             'CAL1': 'JL. MELATI 1', 'CAL2': 'SOLO', 'CALERGI': 'SULFA', 'CNOTELP': '0812 0000 0001'},
            {'CNOMR': 'B2', 'CNMPASIEN': 'ŸSITI', 'DTGLAHIR': '10850101', 'CAL1': 'JL. MAWAR 6',
             'CAL2': 'NIK: 9999 04 410190 0002'},
            {'CNOMR': 'C3', 'CNMPASIEN': 'NAMA LAMA', 'CKELAMIN': 'P', 'DTGLAHIR': '19900101', 'CAL1': 'KLATEN'},
            {'CNOMR': 'D4', 'CNMPASIEN': 'TERHAPUS', 'deleted': True},
            {'CNOMR': 'E5', 'CNMPASIEN': 'AYAH', 'CKELAMIN': 'L', 'DTGLAHIR': '19700101', 'CAL1': 'SOLO',
             'CAL2': '9999040101700001'},
            {'CNOMR': 'F6', 'CNMPASIEN': 'ANAK', 'CKELAMIN': 'L', 'DTGLAHIR': '20100101', 'CAL1': 'SOLO',
             'CAL2': '9999 04 010170 0001'},
        ])
        self.rm_rows = [
            {'CNOMR': 'A1', 'TGL': '20240102', 'COL_ID': '20240102-09:15:00-_A', 'ANAMNESA': 'BATUK 3 HARI',
             'PERIKSA': 'T 120/80', 'DIAGNOSA': 'ISPA', 'PENGOBATAN': 'PCT 3X1', 'CRUJUKKE': 'RS. Hermina',
             'CKDSAKIT': 'j06.9', 'CNMSAKIT': 'ACUTE UPPER RESPIRATORY INFECTION', 'DTGJANJI': '20240109'},
            {'CNOMR': 'A1', 'TGL': '20240102', 'COL_ID': '20240102-08:00:00-_B'},  # blank registration row
            {'CNOMR': 'B2', 'TGL': '20240102', 'COL_ID': '20240104-08:30:00-_C', 'ANAMNESA': 'PUSING\x02'},
            {'CNOMR': 'C3', 'TGL': '22020228', 'COL_ID': '20240105-10:00:00-_D', 'PENGOBATAN': 'KB 3 BLN'},
            {'CNOMR': 'Z9', 'TGL': '20240102', 'COL_ID': '20240102-11:00:00-_E', 'ANAMNESA': 'PASIEN HILANG'},
        ]
        write_dbf(self.dir / 'rm.dbf', RM_DBF, self.rm_rows)

    def run_import(self, *args):
        out = StringIO()
        call_command('import_medpp', str(self.dir), *args, stdout=out)
        return out.getvalue()

    def test_import(self):
        make_patient(no_rm='C3', nama='Nama Baru')
        Visit.objects.create(patient=make_patient(nama='Pasien EMR'), doctor=self.doctor, tanggal=date(2024, 1, 2))
        output = self.run_import()

        budi = Patient.objects.get(no_rm='A1')
        self.assertEqual(
            (budi.nama, budi.jenis_kelamin, budi.tanggal_lahir, budi.alamat, budi.alergi, budi.no_telp, budi.nik),
            ('BUDI SANTOSO', 'L', date(1980, 1, 1), 'JL. MELATI 1, SOLO', 'SULFA', '0812 0000 0001', None),
        )
        siti = Patient.objects.get(no_rm='B2')
        self.assertEqual(
            (siti.nama, siti.jenis_kelamin, siti.tanggal_lahir, siti.nik, siti.alamat),
            ('SITI', '', None, '9999044101900002', 'JL. MAWAR 6'),
        )
        self.assertEqual(siti.jk_umur, '')
        self.assertEqual(Patient.objects.get(no_rm='C3').nama, 'Nama Baru')
        self.assertFalse(Patient.objects.filter(no_rm='D4').exists())
        # A NIK typed on two patients cannot go in the (unique) NIK field, so it stays in the address.
        self.assertEqual(
            list(Patient.objects.filter(no_rm__in=['E5', 'F6']).order_by('no_rm').values_list('nik', 'alamat')),
            [(None, 'SOLO, 9999040101700001'), (None, 'SOLO, 9999 04 010170 0001')],
        )

        visit = budi.visits.get()
        self.assertEqual((visit.status, visit.doctor, visit.tanggal), (Visit.Status.SUDAH, self.doctor, date(2024, 1, 2)))
        self.assertEqual(visit.subjective, 'BATUK 3 HARI')
        self.assertEqual(visit.plan, 'PCT 3X1\nRujuk: RS. Hermina\nKontrol: 09-01-2024')
        self.assertEqual(
            [(d.code, d.name) for d in visit.diagnoses.all()], [('J06.9', 'ACUTE UPPER RESPIRATORY INFECTION')],
        )
        self.assertEqual(timezone.localtime(visit.examined_at).strftime('%H:%M'), '09:15')
        # Backdated note: TGL is the visit date, numbered after the visit already in the EMR that day.
        self.assertEqual(siti.visits.get().subjective, 'PUSING')
        self.assertEqual(
            list(Visit.objects.filter(tanggal=date(2024, 1, 2)).values_list('patient__nama', 'no_antrian')),
            [('Pasien EMR', 1), ('SITI', 2), ('BUDI SANTOSO', 3)],
        )
        self.assertEqual(Visit.objects.create(patient=budi, doctor=self.doctor, tanggal=date(2024, 1, 2)).no_antrian, 4)
        # An impossible visit date falls back to the day the note was typed.
        self.assertEqual(Patient.objects.get(no_rm='C3').visits.get().tanggal, date(2024, 1, 5))

        self.assertIn('4 new, 1 already present', output)
        self.assertIn('3 new, 0 imported before, 1 empty rows skipped', output)
        self.assertIn('B2 (10850101)', output)
        self.assertIn('E5 9999040101700001, F6 9999040101700001', output)
        self.assertIn('Z9 2024-01-02', output)
        self.assertTrue(AuditLog.objects.filter(action='import_medpp').exists())

    def test_running_again_only_adds_new_visits(self):
        self.run_import()
        self.rm_rows.append({'CNOMR': 'A1', 'TGL': '20240201', 'COL_ID': '20240201-09:00:00-_F', 'ANAMNESA': 'KONTROL'})
        write_dbf(self.dir / 'rm.dbf', RM_DBF, self.rm_rows)
        output = self.run_import()
        self.assertIn('0 new, 5 already present', output)
        self.assertIn('1 new, 3 imported before', output)
        self.assertEqual(Visit.objects.count(), 4)

    def test_registration_time_orders_the_patient_list(self):
        write_dbf(self.dir / 'DAFTAR.DBF', [('CNOMR', 'C', 20), ('DTGDAFTAR', 'D', 8), ('CJAM', 'C', 10)], [
            {'CNOMR': 'A1', 'DTGDAFTAR': '20240102', 'CJAM': '07:55:00'},
            {'CNOMR': 'A1', 'DTGDAFTAR': '20231120', 'CJAM': '17:05:00'},
        ])
        earlier = make_patient(nama='Didaftar di EMR')
        Patient.objects.filter(pk=earlier.pk).update(created_at=timezone.make_aware(datetime(2024, 1, 3, 12, 0)))
        self.run_import()
        later = make_patient(nama='Pasien baru')

        def registered(no_rm):
            return timezone.localtime(Patient.objects.get(no_rm=no_rm).created_at).strftime('%Y-%m-%d %H:%M')
        self.assertEqual(registered('A1'), '2023-11-20 17:05')  # first entry in daftar.dbf
        self.assertEqual(registered('B2'), '2024-01-04 08:30')  # not in daftar.dbf: its first note
        self.assertEqual(registered('E5'), '2024-01-05 10:00')  # no date anywhere: the record before it (C3)

        self.client.force_login(self.doctor)
        order = [p.no_rm for p in self.client.get(reverse('rm_list')).context['page_obj']]
        self.assertEqual(order, [later.no_rm, 'F6', 'E5', 'C3', 'B2', earlier.no_rm, 'A1'])

    def test_dry_run_saves_nothing(self):
        output = self.run_import('--dry-run')
        self.assertIn('Dry run', output)
        self.assertFalse(Patient.objects.exists())
        self.assertFalse(Visit.objects.exists())
        self.assertFalse(Counter.objects.filter(key__startswith='antrian:').exists())

    def test_icd_codes_are_added_not_overwritten(self):
        Icd10.objects.create(code='A00', name='Kolera')
        write_dbf(self.dir / 'ICD.DBF', [('CKDSAKIT', 'C', 20), ('CNMSAKIT', 'C', 250), ('LNONICD', 'L', 1)], [
            {'CKDSAKIT': 'A00', 'CNMSAKIT': 'CHOLERA', 'LNONICD': 'F'},
            {'CKDSAKIT': 'A01', 'CNMSAKIT': 'TYPHOID AND PARATYPHOID FEVERS', 'LNONICD': 'F'},
        ])
        self.run_import('--icd')
        self.assertEqual(dict(Icd10.objects.values_list('code', 'name')), {
            'A00': 'Kolera', 'A01': 'TYPHOID AND PARATYPHOID FEVERS',
        })

    def test_doctor_must_be_chosen_when_there_are_several(self):
        make_user('dok2', 'dokter')
        with self.assertRaisesMessage(CommandError, 'dok, dok2'):
            self.run_import()
        with self.assertRaisesMessage(CommandError, 'not an active user with the Dokter role'):
            self.run_import('--dokter', 'nobody')
        self.run_import('--dokter', 'dok2')
        self.assertEqual(set(Visit.objects.values_list('doctor__username', flat=True)), {'dok2'})

    def test_patient_without_birth_date_renders(self):
        self.run_import()
        siti = Patient.objects.get(no_rm='B2')
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get(reverse('rm_list'), {'q': 'siti'}), 'SITI')
        self.assertContains(self.client.get(reverse('rm_detail', args=[siti.pk])), 'PUSING')
        res = self.client.post(reverse('rm_edit', args=[siti.pk]), {'nama': 'SITI', 'alamat': 'JL. MAWAR 6'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('tanggal_lahir', res.context['form'].errors)
        self.assertIn('jenis_kelamin', res.context['form'].errors)
