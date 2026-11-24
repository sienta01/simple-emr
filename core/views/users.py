from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from ..audit import audit
from ..forms import UserForm
from ..models import User
from ..permissions import require


@require('users')
def user_list(request):
    users = User.objects.prefetch_related('groups').order_by('-is_active', 'display_name', 'username')
    return render(request, 'core/users/list.html', {'users': users})


@require('users')
def user_form(request, pk=None):
    instance = get_object_or_404(User, pk=pk) if pk else User()
    form = UserForm(request.POST or None, instance=instance, editor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        audit(request, 'buat_pengguna' if pk is None else 'ubah_pengguna', detail={
            'username': user.username, 'roles': sorted(form.cleaned_data['roles']), 'aktif': user.is_active,
            'ganti_password': bool(form.cleaned_data.get('password1')),
        })
        messages.success(request, f'Pengguna {user} tersimpan.')
        return redirect('user_list')
    return render(request, 'core/users/form.html', {'form': form, 'target': instance})
