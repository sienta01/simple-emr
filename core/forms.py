import json
import re

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ROLE_CHOICES, Patient, User
from .permissions import ROLES

NIK_RE = re.compile(r'^\d{16}$')


def validate_nik(nik, exclude_pk=None):
    if not NIK_RE.match(nik):
        raise ValidationError('NIK harus 16 digit angka.')
    others = Patient.objects.filter(nik=nik)
    if exclude_pk:
        others = others.exclude(pk=exclude_pk)
    other = others.first()
    if other:
        raise ValidationError(f'NIK sudah terdaftar atas nama {other.nama} (RM {other.no_rm}).')


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            'nik', 'nama', 'jenis_kelamin', 'tempat_lahir', 'tanggal_lahir', 'pekerjaan',
            'alamat', 'no_telp', 'nama_wali', 'hubungan_wali',
        ]
        widgets = {
            'nik': forms.TextInput(attrs={
                'inputmode': 'numeric', 'maxlength': 16, 'autocomplete': 'off',
                'placeholder': '16 digit — boleh dikosongkan',
            }),
            'tanggal_lahir': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'jenis_kelamin': forms.RadioSelect,
            'alamat': forms.TextInput(attrs={'autocomplete': 'off'}),
            'no_telp': forms.TextInput(attrs={'inputmode': 'tel', 'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['jenis_kelamin'].choices = Patient.JenisKelamin.choices
        self.fields['hubungan_wali'].choices = [('', '— pilih —')] + list(Patient.Hubungan.choices)
        self.fields['tanggal_lahir'].widget.attrs['max'] = timezone.localdate().isoformat()
        self.fields['nama'].widget.attrs['autocomplete'] = 'off'
        for name, field in self.fields.items():
            if name != 'jenis_kelamin':
                field.widget.attrs.setdefault('class', 'input')

    def clean_nik(self):
        nik = (self.cleaned_data.get('nik') or '').replace(' ', '')
        if not nik:
            return None
        validate_nik(nik, exclude_pk=self.instance.pk)
        return nik

    def clean_nama(self):
        return ' '.join(self.cleaned_data['nama'].split())

    def clean_tanggal_lahir(self):
        dob = self.cleaned_data['tanggal_lahir']
        if dob > timezone.localdate():
            raise ValidationError('Tanggal lahir tidak boleh di masa depan.')
        if dob.year < 1900:
            raise ValidationError('Tanggal lahir tidak valid.')
        return dob


class SoapForm(forms.Form):
    subjective = forms.CharField(label='Subjektif', widget=forms.Textarea(attrs={'rows': 4}))
    objective = forms.CharField(label='Objektif', widget=forms.Textarea(attrs={'rows': 4}))
    assessment = forms.CharField(label='Asesmen', widget=forms.Textarea(attrs={'rows': 3}))
    plan = forms.CharField(label='Plan', widget=forms.Textarea(attrs={'rows': 4}))
    diagnoses = forms.CharField(required=False, widget=forms.HiddenInput)
    # ICD-9-CM, entered on the Tindakan tab and saved together with the note.
    procedures = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, readonly=False, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'input')
            field.widget.attrs['id'] = f'id_{name}'
            if readonly:
                field.disabled = True

    def clean_diagnoses(self):
        return clean_codes(self.cleaned_data.get('diagnoses'), 'ICD-X')

    def clean_procedures(self):
        return clean_codes(self.cleaned_data.get('procedures'), 'ICD-9-CM')


def clean_codes(raw, label):
    """JSON list of {code, name} from a chips field -> de-duplicated, trimmed list."""
    try:
        items = json.loads(raw or '[]')
    except ValueError:
        raise ValidationError(f'Format {label} tidak valid.')
    if not isinstance(items, list):
        raise ValidationError(f'Format {label} tidak valid.')
    cleaned, seen = [], set()
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip().upper()[:10]
        name = ' '.join(str(item.get('name') or '').split())[:300]
        if not name and not code:
            continue
        key = (code, name.lower())
        if key not in seen:
            seen.add(key)
            cleaned.append({'code': code, 'name': name or code})
    return cleaned


class LaporanForm(forms.Form):
    bulan = forms.CharField(label='Bulan pelaporan', widget=forms.TextInput(attrs={'type': 'month', 'placeholder': 'YYYY-MM'}))
    dokter = forms.ModelChoiceField(label='Dokter (DPM)', queryset=User.objects.none(), empty_label=None)
    puskesmas = forms.CharField(label='Puskesmas', max_length=100, required=False)
    usia_min = forms.IntegerField(label='Usia dari', min_value=0, max_value=150)
    usia_max = forms.IntegerField(label='Usia sampai', min_value=0, max_value=150)

    def __init__(self, *args, doctors, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['dokter'].queryset = doctors
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'input')

    def clean_bulan(self):
        match = re.fullmatch(r'(\d{4})-(\d{2})', self.cleaned_data['bulan'].strip())
        if not match or int(match.group(1)) < 1900 or not 1 <= int(match.group(2)) <= 12:
            raise ValidationError('Pilih bulan pelaporan.')
        return int(match.group(1)), int(match.group(2))

    def clean(self):
        data = super().clean()
        if data.get('usia_min') is not None and data.get('usia_max') is not None and data['usia_min'] > data['usia_max']:
            self.add_error('usia_max', 'Batas usia atas lebih kecil dari batas bawah.')
        return data


class UserForm(forms.ModelForm):
    roles = forms.MultipleChoiceField(
        label='Peran', choices=ROLE_CHOICES, required=False, widget=forms.CheckboxSelectMultiple,
    )
    password1 = forms.CharField(
        label='Kata sandi', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'input'}),
    )
    password2 = forms.CharField(
        label='Ulangi kata sandi', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'class': 'input'}),
    )

    class Meta:
        model = User
        fields = ['username', 'display_name', 'is_active']
        labels = {'username': 'Nama pengguna (untuk login)', 'is_active': 'Akun aktif'}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        self.creating = self.instance.pk is None
        self.fields['username'].widget.attrs.update({'class': 'input', 'autocomplete': 'off'})
        self.fields['display_name'].widget.attrs.update({'class': 'input', 'autocomplete': 'off'})
        self.fields['roles'].help_text = ' '.join(f'{label}: {desc}.' for label, desc in ROLES.values())
        # Spell out whatever password rules .env switched on, so nobody has to guess.
        rules = ' '.join(password_validation.password_validators_help_texts())
        if self.creating:
            self.fields['password1'].required = True
            self.fields['password2'].required = True
            self.fields['is_active'].initial = True
            self.fields['password1'].help_text = rules
        else:
            self.fields['roles'].initial = sorted(self.instance.roles & set(ROLES))
            self.fields['password1'].help_text = f'Kosongkan jika tidak ingin mengganti kata sandi. {rules}'.strip()

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 or p2:
            if p1 != p2:
                self.add_error('password2', 'Kata sandi tidak sama.')
            else:
                try:
                    password_validation.validate_password(p1, self.instance)
                except ValidationError as exc:
                    self.add_error('password1', exc)
        if self.editor is not None and self.instance.pk == self.editor.pk:
            if 'admin' not in cleaned.get('roles', []):
                self.add_error('roles', 'Anda tidak dapat menghapus peran Admin dari akun Anda sendiri.')
            if not cleaned.get('is_active'):
                self.add_error('is_active', 'Anda tidak dapat menonaktifkan akun Anda sendiri.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        roles = set(self.cleaned_data.get('roles') or [])
        user.is_staff = user.is_superuser = 'admin' in roles
        if self.cleaned_data.get('password1'):
            user.set_password(self.cleaned_data['password1'])
        user.save()
        user.groups.set(Group.objects.filter(name__in=roles))
        return user
