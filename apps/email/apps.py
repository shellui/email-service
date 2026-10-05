from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_library(sender, **kwargs):
    from apps.email.library import sync_builtins

    sync_builtins()


class EmailConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.email'
    label = 'email'
    verbose_name = 'Email'

    def ready(self):
        post_migrate.connect(_sync_library, sender=self, dispatch_uid='email-library-sync')
        from corsheaders.signals import check_request_enabled

        from apps.email.newsletters import cors_for_subscribe

        check_request_enabled.connect(cors_for_subscribe, dispatch_uid='email-newsletter-cors')
