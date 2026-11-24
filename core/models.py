import os
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .permissions import ROLES
from .utils import ddmmyyyy, format_age, is_allergy

ROLE_CHOICES = [(key, label) for key, (label, _description) in ROLES.items()]


class User(AbstractUser):
    """Roles are Django groups named after ROLE_CHOICES keys; a user may hold several."""

    display_name = models.CharField(
        'nama tampilan', max_length=150, blank=True,
        help_text='Nama yang tampil di aplikasi, mis. "dr. Andi Pratama".',
    )

    class Meta:
        verbose_name = 'pengguna'
        verbose_name_plural = 'pengguna'

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

    @property
    def roles(self):
        if not hasattr(self, '_roles'):
            self._roles = set(self.groups.values_list('name', flat=True))
            if self.is_superuser:
                self._roles.add('admin')
        return self._roles

    def has_role(self, *names):
        return bool(self.roles.intersection(names))

    @property
    def role_labels(self):
        return [label for key, label in ROLE_CHOICES if key in self.roles]


class Counter(models.Model):
    """Gap-free sequences (RM numbers, daily queue numbers) that are safe under concurrency."""

    key = models.CharField(max_length=50, primary_key=True)
    value = models.BigIntegerField(default=0)

    @classmethod
    def next(cls, key):
        with transaction.atomic():
            cls.objects.get_or_create(key=key)
            # The UPDATE takes a row lock, so concurrent callers queue up here.
            cls.objects.filter(key=key).update(value=F('value') + 1)
            return cls.objects.get(key=key).value


def patient_foto_upload_to(instance, filename):
    return f'foto/{instance.no_rm}/{uuid.uuid4().hex}.jpg'


class Patient(models.Model):
    class JenisKelamin(models.TextChoices):
        L = 'L', 'Laki-laki'
        P = 'P', 'Perempuan'

    class Hubungan(models.TextChoices):
        AYAH = 'ayah', 'Ayah'
        IBU = 'ibu', 'Ibu'
        ANAK = 'anak', 'Anak'
        SAUDARA = 'saudara', 'Saudara'
        KERABAT = 'kerabat', 'Kerabat'

    no_rm = models.CharField('No. RM', max_length=20, unique=True)
    nik = models.CharField('NIK', max_length=16, unique=True, null=True, blank=True)
    nama = models.CharField('nama lengkap', max_length=150, db_index=True)
    jenis_kelamin = models.CharField('jenis kelamin', max_length=1, choices=JenisKelamin.choices)
    tempat_lahir = models.CharField('tempat lahir', max_length=100, blank=True)
    # Required on every form; empty only for imported records whose old date of birth was missing or invalid.
    tanggal_lahir = models.DateField('tanggal lahir', null=True)
    pekerjaan = models.CharField('pekerjaan', max_length=100, blank=True)
    alamat = models.CharField('alamat', max_length=255)
    no_telp = models.CharField('no. telp', max_length=30, blank=True)
    nama_wali = models.CharField('nama wali pasien', max_length=150, blank=True)
    hubungan_wali = models.CharField(
        'hubungan dengan pasien', max_length=10, choices=Hubungan.choices, blank=True,
    )
    alergi = models.TextField('alergi', blank=True)
    # Face photo (webcam or upload) so staff can recognise the patient; served by core.views.patients.
    foto = models.FileField('foto', upload_to=patient_foto_upload_to, max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        ordering = ['nama']
        verbose_name = 'pasien'
        verbose_name_plural = 'pasien'

    def __str__(self):
        return f'{self.nama} ({self.no_rm})'

    def save(self, *args, **kwargs):
        self.nik = self.nik or None
        if self.no_rm:
            return super().save(*args, **kwargs)
        # Same transaction as the insert, so a failed save does not burn an RM number.
        with transaction.atomic():
            self.no_rm = self._next_no_rm()
            return super().save(*args, **kwargs)

    @staticmethod
    def _next_no_rm():
        while True:
            candidate = str(Counter.next('no_rm')).zfill(settings.RM_DIGITS)
            # RM numbers entered by hand (e.g. migrated paper charts) are skipped over.
            if not Patient.objects.filter(no_rm=candidate).exists():
                return candidate

    @property
    def umur(self):
        return format_age(self.tanggal_lahir)

    @property
    def jk_umur(self):
        return ' / '.join(part for part in (self.jenis_kelamin, self.umur) if part)

    @property
    def ttl(self):
        return ', '.join(part for part in (self.tempat_lahir, ddmmyyyy(self.tanggal_lahir)) if part)

    @property
    def has_allergy(self):
        return is_allergy(self.alergi)

    @property
    def foto_url(self):
        if not self.foto:
            return ''
        # Every new photo gets a new file name, so browsers may cache each URL for long.
        return f"{reverse('patient_foto', args=[self.pk])}?v={os.path.basename(self.foto.name)}"

    @property
    def wali(self):
        if not self.nama_wali:
            return ''
        if self.hubungan_wali:
            return f'{self.nama_wali} ({self.get_hubungan_wali_display().lower()})'
        return self.nama_wali


class Visit(models.Model):
    class Status(models.TextChoices):
        BELUM = 'belum', 'Belum diperiksa'
        SUDAH = 'sudah', 'Sudah diperiksa'
        BATAL = 'batal', 'Batal'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='visits', verbose_name='pasien')
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='visits_as_doctor', verbose_name='dokter',
    )
    tanggal = models.DateField(default=timezone.localdate, db_index=True)
    no_antrian = models.PositiveIntegerField('no. antrian')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BELUM, db_index=True)
    registered_at = models.DateTimeField('jam daftar', default=timezone.now)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    subjective = models.TextField('S (subjektif)', blank=True)
    objective = models.TextField('O (objektif)', blank=True)
    assessment = models.TextField('A (asesmen)', blank=True)
    plan = models.TextField('P (plan)', blank=True)
    examined_at = models.DateTimeField('waktu periksa', null=True, blank=True)
    examined_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    # Set when the doctor opens the patient: the front desk sees "Dipanggil" until the SOAP is saved.
    called_at = models.DateTimeField('waktu dipanggil', null=True, blank=True)
    called_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    # Bumped on every change; the queue screens poll it to know when to refresh.
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    # Source record of an imported visit (e.g. "medpp:<id>"), so running the import again skips it.
    legacy_id = models.CharField(max_length=100, unique=True, null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-tanggal', 'no_antrian']
        verbose_name = 'kunjungan'
        verbose_name_plural = 'kunjungan'
        constraints = [
            models.UniqueConstraint(fields=['tanggal', 'no_antrian'], name='antrian_unik_per_hari'),
        ]

    def __str__(self):
        return f'{self.tanggal:%d-%m-%Y} #{self.no_antrian} {self.patient.nama}'

    @property
    def is_called(self):
        return self.status == self.Status.BELUM and self.called_at is not None

    @property
    def status_key(self):
        return 'dipanggil' if self.is_called else self.status

    @property
    def status_label(self):
        return 'Dipanggil' if self.is_called else self.get_status_display()

    def save(self, *args, **kwargs):
        if self.no_antrian:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            self.no_antrian = Counter.next(f'antrian:{self.tanggal.isoformat()}')
            return super().save(*args, **kwargs)


class Diagnosis(models.Model):
    """ICD-X entries of a visit. Code and name are copied, so free-text diagnoses work too."""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='diagnoses')
    code = models.CharField('kode', max_length=10, blank=True)
    name = models.CharField('diagnosis', max_length=300)
    urutan = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['urutan', 'id']
        verbose_name = 'diagnosis'
        verbose_name_plural = 'diagnosis'

    def __str__(self):
        return f'{self.code} {self.name}'.strip()

    def as_dict(self):
        return {'code': self.code, 'name': self.name}


class Procedure(models.Model):
    """ICD-9-CM procedures (Tindakan) of a visit. Code and name are copied, so free-text entries work too."""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='procedures')
    code = models.CharField('kode', max_length=10, blank=True)
    name = models.CharField('tindakan', max_length=300)
    urutan = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['urutan', 'id']
        verbose_name = 'tindakan'
        verbose_name_plural = 'tindakan'

    def __str__(self):
        return f'{self.code} {self.name}'.strip()

    def as_dict(self):
        return {'code': self.code, 'name': self.name}


class Icd10(models.Model):
    code = models.CharField('kode', max_length=10, unique=True)
    name = models.CharField('nama', max_length=300, help_text='Judul resmi WHO (bahasa Inggris).')
    # Extra search words (e.g. Indonesian terms); never shown or stored in the chart.
    alias = models.CharField('kata kunci', max_length=300, blank=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'ICD-10'
        verbose_name_plural = 'ICD-10'

    def __str__(self):
        return f'{self.code} {self.name}'


class Icd9(models.Model):
    """ICD-9-CM procedure codes (the edition BPJS / INA-CBG uses), e.g. 86.59."""

    code = models.CharField('kode', max_length=10, unique=True)
    name = models.CharField('nama', max_length=300, help_text='Judul resmi ICD-9-CM (bahasa Inggris).')
    # Extra search words (e.g. Indonesian terms); never shown or stored in the chart.
    alias = models.CharField('kata kunci', max_length=300, blank=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'ICD-9-CM'
        verbose_name_plural = 'ICD-9-CM'

    def __str__(self):
        return f'{self.code} {self.name}'


def penunjang_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or '.bin'
    return f'penunjang/{instance.patient.no_rm}/{uuid.uuid4().hex}{ext}'


class PenunjangQuerySet(models.QuerySet):
    def active(self):
        return self.filter(deleted_at__isnull=True)


class Penunjang(models.Model):
    """One page of an ancillary result (lab, EKG, radiology). Higher position = later page."""

    class Kategori(models.TextChoices):
        LAB = 'lab', 'Lab'
        EKG = 'ekg', 'EKG'
        RADIOLOGI = 'radiologi', 'Radiologi'
        LAINNYA = 'lainnya', 'Lainnya'

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='penunjang', verbose_name='pasien')
    visit = models.ForeignKey(
        Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name='penunjang', verbose_name='kunjungan',
    )
    kategori = models.CharField(max_length=20, choices=Kategori.choices, default=Kategori.LAB)
    file = models.FileField(upload_to=penunjang_upload_to, max_length=255)
    thumbnail = models.FileField(upload_to=penunjang_upload_to, max_length=255, blank=True)
    original_name = models.CharField('nama file asli', max_length=255, blank=True)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField(default=0)
    note = models.TextField('catatan', blank=True)
    position = models.PositiveIntegerField(default=0, db_index=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    objects = PenunjangQuerySet.as_manager()

    class Meta:
        ordering = ['patient', 'position']
        verbose_name = 'penunjang'
        verbose_name_plural = 'penunjang'

    def __str__(self):
        return f'{self.patient.no_rm} {self.get_kategori_display()} #{self.pk}'

    @property
    def is_pdf(self):
        return self.content_type == 'application/pdf'


class AuditLog(models.Model):
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    username = models.CharField(max_length=150, blank=True, db_index=True)
    action = models.CharField(max_length=50, db_index=True)
    patient = models.ForeignKey(Patient, null=True, blank=True, on_delete=models.SET_NULL)
    detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'log audit'
        verbose_name_plural = 'log audit'

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} {self.username} {self.action}'
