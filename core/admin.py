from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AuditLog, Counter, Diagnosis, Icd9, Icd10, Patient, Penunjang, Procedure, User, Visit
from .views.auth import AdminLoginForm

admin.site.login_form = AdminLoginForm


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (('EMR', {'fields': ('display_name',)}),)
    list_display = ('username', 'display_name', 'is_active', 'last_login')


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('no_rm', 'nama', 'jenis_kelamin', 'tanggal_lahir', 'nik', 'alamat')
    search_fields = ('no_rm', 'nama', 'nik', 'alamat')
    readonly_fields = ('created_at', 'updated_at', 'created_by')
    exclude = ('foto',)  # set from the identity box, which converts and replaces it properly


class DiagnosisInline(admin.TabularInline):
    model = Diagnosis
    extra = 0


class ProcedureInline(admin.TabularInline):
    model = Procedure
    extra = 0


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ('tanggal', 'no_antrian', 'patient', 'doctor', 'status')
    list_filter = ('status', 'tanggal', 'doctor')
    search_fields = ('patient__nama', 'patient__no_rm')
    raw_id_fields = ('patient',)
    inlines = [DiagnosisInline, ProcedureInline]


@admin.register(Icd10, Icd9)
class IcdAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'alias')
    search_fields = ('code', 'name', 'alias')


@admin.register(Penunjang)
class PenunjangAdmin(admin.ModelAdmin):
    list_display = ('patient', 'kategori', 'uploaded_at', 'uploaded_by', 'deleted_at')
    list_filter = ('kategori', ('deleted_at', admin.EmptyFieldListFilter))
    search_fields = ('patient__nama', 'patient__no_rm', 'note')
    raw_id_fields = ('patient', 'visit')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'username', 'action', 'patient', 'ip')
    list_filter = ('action',)
    search_fields = ('username', 'patient__nama', 'patient__no_rm', 'detail')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Counter)
