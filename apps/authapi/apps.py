from django.apps import AppConfig


class AuthapiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.authapi'
    label = 'authapi'
    verbose_name = 'Identity JWKS auth'

    def ready(self):
        from django.conf import settings

        from . import checks  # noqa: F401
        from . import openapi  # noqa: F401

        simple = getattr(settings, 'SIMPLE_JWT', None)
        if simple:
            from rest_framework_simplejwt.settings import api_settings

            for key, value in simple.items():
                setattr(api_settings, key, value)
