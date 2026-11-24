from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import AuthenticationForm
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from ..audit import audit
from ..models import AuditLog
from ..permissions import can
from ..utils import is_mobile

MAX_FAILED_LOGINS = 10
LOCKOUT_WINDOW = timedelta(minutes=15)


class LockoutMixin:
    """Blocks a username for a while after too many failed logins; shared by both login pages."""

    locked_message = 'Terlalu banyak percobaan masuk yang gagal. Coba lagi dalam 15 menit.'

    def clean(self):
        username = self.cleaned_data.get('username')
        if username:
            failures = AuditLog.objects.filter(
                action='login_gagal', username__iexact=username,
                timestamp__gte=timezone.now() - LOCKOUT_WINDOW,
            ).count()
            if failures >= MAX_FAILED_LOGINS:
                raise forms.ValidationError(self.locked_message, code='locked')
        return super().clean()


class LoginForm(LockoutMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': 'input', 'autofocus': True})
        self.fields['password'].widget.attrs.update({'class': 'input'})


class AdminLoginForm(LockoutMixin, AdminAuthenticationForm):
    """Login form of /admin/ (set in core.admin)."""


class LoginView(auth_views.LoginView):
    template_name = 'core/login.html'
    form_class = LoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        # Phones land on the upload-only page (if the role may upload); everything else on the role's home.
        if is_mobile(self.request) and can(self.request.user, 'penunjang'):
            return reverse('mobile_home')
        return reverse('home')


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'core/account/password.html'
    success_url = reverse_lazy('home')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs['class'] = 'input'
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        audit(self.request, 'ganti_password')
        messages.success(self.request, 'Kata sandi berhasil diganti.')
        return response


def home(request):
    user = request.user
    if user.has_role('dokter') and can(user, 'pemeriksaan'):
        return redirect('pemeriksaan')
    for url_name, perm in (('pendaftaran', 'pendaftaran'), ('pemeriksaan', 'pemeriksaan'), ('rm_list', 'rekam_medis')):
        if can(user, perm):
            return redirect(url_name)
    return render(request, 'core/no_role.html', status=403)


@login_not_required
def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return HttpResponse('ok', content_type='text/plain')
