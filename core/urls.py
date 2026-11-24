from django.contrib.auth import views as auth_views
from django.urls import path

from .views import auth, laporan, mobile, patients, penunjang, users, visits

urlpatterns = [
    path('', auth.home, name='home'),
    path('login/', auth.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('akun/password/', auth.PasswordChangeView.as_view(), name='password_change'),
    path('healthz', auth.healthz, name='healthz'),

    path('pendaftaran/', visits.pendaftaran_list, name='pendaftaran'),
    path('pendaftaran/daftar/', visits.api_register_visit, name='api_register_visit'),
    path('pendaftaran/<int:pk>/batal/', visits.visit_cancel, name='visit_cancel'),

    path('pemeriksaan/', visits.pemeriksaan_list, name='pemeriksaan'),
    path('pemeriksaan/<int:pk>/', visits.pemeriksaan_detail, name='pemeriksaan_detail'),
    path('pemeriksaan/<int:pk>/kembalikan/', visits.visit_uncall, name='visit_uncall'),
    path('antrian/versi/', visits.queue_version, name='queue_version'),

    path('rekam-medis/', patients.rm_list, name='rm_list'),
    path('rekam-medis/<int:pk>/', patients.rm_detail, name='rm_detail'),
    path('rekam-medis/<int:pk>/ubah/', patients.rm_edit, name='rm_edit'),
    path('rekam-medis/<int:pk>/kartu/', patients.kartu_cetak, name='kartu_cetak'),

    path('api/pasien/cari/', patients.api_patient_search, name='api_patient_search'),
    path('api/pasien/kartu/', patients.api_patient_by_card, name='api_patient_by_card'),
    path('api/pasien/baru/', patients.api_patient_create, name='api_patient_create'),
    path('api/pasien/<int:pk>/ubah/', patients.api_patient_update, name='api_patient_update'),
    path('api/pasien/<int:pk>/nik/', patients.api_patient_nik, name='api_patient_nik'),
    path('api/pasien/<int:pk>/alergi/', patients.api_patient_alergi, name='api_patient_alergi'),
    path('api/pasien/<int:pk>/foto/', patients.api_patient_foto, name='api_patient_foto'),
    path('api/pasien/<int:pk>/foto/hapus/', patients.api_patient_foto_delete, name='api_patient_foto_delete'),
    path('api/icd10/', patients.api_icd10, name='api_icd10'),
    path('api/icd9/', patients.api_icd9, name='api_icd9'),

    path('pasien/<int:pk>/foto', patients.patient_foto, name='patient_foto'),

    path('pasien/<int:patient_id>/penunjang/', penunjang.penunjang_list, name='penunjang_list'),
    path('pasien/<int:patient_id>/penunjang/upload/', penunjang.penunjang_upload, name='penunjang_upload'),
    path('pasien/<int:patient_id>/penunjang/urutan/', penunjang.penunjang_reorder, name='penunjang_reorder'),
    path('pasien/<int:patient_id>/penunjang/qr.svg', penunjang.penunjang_qr, name='penunjang_qr'),
    path('penunjang/<int:pk>/', penunjang.penunjang_update, name='penunjang_update'),
    path('penunjang/<int:pk>/hapus/', penunjang.penunjang_delete, name='penunjang_delete'),
    path('penunjang/<int:pk>/file', penunjang.penunjang_file, name='penunjang_file'),
    path('penunjang/<int:pk>/thumb', penunjang.penunjang_thumb, name='penunjang_thumb'),

    path('laporan/', laporan.laporan, name='laporan'),

    path('m/', mobile.mobile_home, name='mobile_home'),
    path('m/pasien/<int:patient_id>/', mobile.mobile_upload, name='mobile_upload'),

    path('pengguna/', users.user_list, name='user_list'),
    path('pengguna/baru/', users.user_form, name='user_create'),
    path('pengguna/<int:pk>/', users.user_form, name='user_edit'),
]
