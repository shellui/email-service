"""Bearer auth: service API keys (``esk_``) or identity-service JWTs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.authapi.authentication import IdentityJWKSAuthentication

logger = logging.getLogger(__name__)

SERVICE_KEY_PREFIX = 'esk_'


@dataclass
class ServicePrincipal:
    """Machine caller authenticated with a service API key."""

    service: str
    client_id: int
    allowed_lanes: tuple[str, ...]
    allowed_template_prefixes: tuple[str, ...]
    allowed_company_ids: tuple[int, ...] | None
    key_prefix: str
    is_staff: bool = False
    is_company_owner: bool = False
    is_service: bool = True
    company_id: int | None = None
    email: str = ''
    user_id: int = 0

    @property
    def pk(self) -> int:
        return self.client_id

    @property
    def id(self) -> int:
        return self.client_id

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def allows_company(self, company_id: int) -> bool:
        if self.allowed_company_ids is None:
            return True
        return int(company_id) in self.allowed_company_ids

    def allows_lane(self, lane: str) -> bool:
        return lane in self.allowed_lanes

    def allows_template(self, template_key: str) -> bool:
        if not self.allowed_template_prefixes:
            return False
        return any(template_key.startswith(prefix) for prefix in self.allowed_template_prefixes)

    def __str__(self) -> str:
        return f'service:{self.service}'


def hash_service_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()


def authenticate_service_key(raw_key: str) -> ServicePrincipal:
    from apps.email.models import ServiceClient

    key_hash = hash_service_key(raw_key)
    client = ServiceClient.objects.filter(key_hash=key_hash, active=True).first()
    if client is None:
        raise exceptions.AuthenticationFailed('Invalid service key.')
    ServiceClient.objects.filter(pk=client.pk).update(last_used_at=timezone.now())
    allowed_companies = client.allowed_company_ids
    company_tuple: tuple[int, ...] | None
    if allowed_companies in (None, []):
        company_tuple = None if not allowed_companies else tuple(int(c) for c in allowed_companies)
    else:
        company_tuple = tuple(int(c) for c in allowed_companies)
    return ServicePrincipal(
        service=client.service,
        client_id=client.pk,
        allowed_lanes=tuple(client.allowed_lanes or []),
        allowed_template_prefixes=tuple(client.allowed_template_prefixes or []),
        allowed_company_ids=company_tuple,
        key_prefix=client.key_prefix,
    )


class ShelluiAuthentication(IdentityJWKSAuthentication):
    """
    ``Authorization: Bearer esk_<token>`` authenticates a service client.

    Any other bearer token is verified as an identity-service JWT (RS256 / JWKS),
    the same path storage-service and hosting-service use.
    """

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode('utf-8')
        if not header:
            return None
        parts = header.split()
        if len(parts) == 2 and parts[0] == self.keyword and parts[1].startswith(SERVICE_KEY_PREFIX):
            principal = authenticate_service_key(parts[1])
            return (principal, parts[1])
        return super().authenticate(request)


def touch_last_used(client_id: int, when: datetime | None = None) -> None:
    logger.debug('service client %s used at %s', client_id, when or timezone.now())
