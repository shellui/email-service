"""Register the local dev stack's service keys. Refuses to run unless DEBUG is on."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.authapi.service_auth import SERVICE_KEY_PREFIX, hash_service_key
from apps.email.keys import issue_service_key
from apps.email.models import ServiceClient

ENV_VAR = 'EMAIL_DEV_SERVICE_KEYS'
# Only identity sends sign-in mail, so only its key gets the auth lane.
DEV_LANES = {'identity': ['auth', 'transactional']}


def lanes_for(service: str) -> list[str]:
    return DEV_LANES.get(service, ['transactional'])


def parse_keys(value: str) -> list[tuple[str, str]]:
    """Parse ``service=esk_...`` pairs separated by commas. Never echoes a key in errors."""
    pairs = []
    for item in filter(None, (part.strip() for part in value.split(','))):
        service, sep, raw = (part.strip() for part in item.partition('='))
        if not sep or not service or not raw.startswith(SERVICE_KEY_PREFIX) or len(raw) < 24:
            raise CommandError(f'{ENV_VAR}: invalid entry for service "{service or "?"}"')
        pairs.append((service, raw))
    return pairs


class Command(BaseCommand):
    help = f'Dev only: register service keys from {ENV_VAR} (service=esk_...,...). Idempotent.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('register_dev_service_keys only runs with DEBUG=true')
        for service, raw in parse_keys(os.getenv(ENV_VAR, '')):
            existing = ServiceClient.objects.filter(key_hash=hash_service_key(raw)).first()
            if existing is None:
                issue_service_key(service=service, name=f'{service} (dev)', raw=raw, allowed_lanes=lanes_for(service))
                self.stdout.write(f'registered {service}')
            elif existing.service != service:
                raise CommandError(f'{ENV_VAR}: key for "{service}" is already registered to another service')
            elif sorted(existing.allowed_lanes or []) != sorted(lanes_for(service)):
                existing.allowed_lanes = lanes_for(service)
                existing.save(update_fields=['allowed_lanes'])
                self.stdout.write(f'{service} lanes updated')
            else:
                self.stdout.write(f'{service} already registered')
