from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'EMR'

    def ready(self):
        from . import signals  # noqa: F401  (registers login audit receivers)
        from .checks import ensure_role_groups  # also registers the role checks

        # The container runs migrate on every start, so new roles get their group automatically.
        post_migrate.connect(ensure_role_groups, sender=self)

        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            pass
        else:
            register_heif_opener()
