"""WSGI config."""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from config.telemetry import configure_telemetry

configure_telemetry()

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
