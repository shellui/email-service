"""ASGI config."""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from config.telemetry import configure_telemetry

configure_telemetry()

from django.core.asgi import get_asgi_application

application = get_asgi_application()
