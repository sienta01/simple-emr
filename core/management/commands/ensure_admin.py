import os
import secrets

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = 'Creates the first admin account (ADMIN_USERNAME / ADMIN_PASSWORD) if no admin exists yet.'

    def handle(self, *args, **options):
        if User.objects.filter(is_superuser=True).exists():
            return
        username = os.environ.get('ADMIN_USERNAME') or 'admin'
        password = os.environ.get('ADMIN_PASSWORD')
        if password == 'change-me' and not settings.DEBUG:  # public placeholder from .env.example
            raise CommandError('ADMIN_PASSWORD is still "change-me"; set your own password in .env.')
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        user = User.objects.create_superuser(username=username, password=password, display_name='Administrator')
        user.groups.add(Group.objects.get(name='admin'))
        self.stdout.write(self.style.SUCCESS(f'Admin account "{username}" created.'))
        if generated:
            self.stdout.write(self.style.WARNING(
                f'ADMIN_PASSWORD was not set; generated password: {password}  (change it after logging in)'
            ))
