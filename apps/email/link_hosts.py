"""Per-company auth link hosts.

The platform list ``EMAIL_AUTH_LINK_HOSTS`` applies to every company. A company
may add exact hostnames, and hosts of its identity application URLs are included
when identity returns them. A host that is not on that union is refused.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import IntegrityError, transaction

from apps.actions.models import EventLog
from apps.email.models import CompanyProfile

MAX_EXTRA_HOSTS = 32
MAX_APPLICATION_HOSTS = 64
_MAX_IDENTITY_BODY = 65_536

EVENT_ADDED = 'email.auth_link_host.added'
EVENT_REMOVED = 'email.auth_link_host.removed'

_LOOPBACK = {'localhost', '127.0.0.1', '::1'}
_LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
_DNS_RE = re.compile(rf'^(?:{_LABEL}\.)+{_LABEL}$')

_ADMIN_TEXT = {
    'invalid': 'Enter a list of hostnames.',
    'invalid_hostname': 'Enter an exact hostname, such as app.example.com.',
    'wildcard_not_allowed': 'Wildcards are not allowed.',
    'ip_not_allowed': 'IP addresses are not allowed.',
    'port_not_allowed': 'Ports are not allowed. Enter the hostname only.',
    'path_not_allowed': 'Paths are not allowed. Enter the hostname only.',
    'scheme_not_allowed': 'Do not include a scheme. Enter the hostname only.',
    'localhost_not_allowed': 'localhost is not allowed in production.',
    'too_many': f'At most {MAX_EXTRA_HOSTS} hostnames.',
}


class HostRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def host_error_text(code: str) -> str:
    return _ADMIN_TEXT.get(code, _ADMIN_TEXT['invalid_hostname'])


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    parts = value.split('.')
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        return False
    return all(0 <= int(part) <= 255 for part in parts)


def _disguised_ip(host: str) -> bool:
    for label in host.split('.'):
        if label.startswith('0x'):
            return True
        if len(label) > 1 and label.isdigit() and label.startswith('0'):
            return True
    return False


def canonical_host_or_none(value: str) -> str | None:
    """Lowercase hostname, or None when ``value`` is not a hostname or IP."""
    raw = (value or '').strip().lower()
    if not raw:
        return None
    if raw.startswith('[') and raw.endswith(']'):
        raw = raw[1:-1]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    if raw.endswith('.'):
        return None
    try:
        encoded = raw.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    if encoded != 'localhost' and not _DNS_RE.fullmatch(encoded):
        return None
    return encoded


def classify_extra_host(raw: str) -> str:
    """Return a canonical extra hostname, or raise ``HostRejected``."""
    if not isinstance(raw, str):
        raise HostRejected('invalid_hostname')
    text = raw.strip().lower()
    if not text:
        raise HostRejected('invalid_hostname')
    if '://' in text or text.startswith('//'):
        raise HostRejected('scheme_not_allowed')
    if any(char in text for char in ' /?#\\'):
        raise HostRejected('path_not_allowed')
    if '*' in text or text.startswith('.'):
        raise HostRejected('wildcard_not_allowed')
    if '@' in text:
        raise HostRejected('invalid_hostname')
    bare = text[1:-1] if text.startswith('[') and text.endswith(']') else text
    if _is_ip_literal(bare):
        raise HostRejected('ip_not_allowed')
    if ':' in bare:
        raise HostRejected('port_not_allowed')
    host = canonical_host_or_none(bare)
    if host is None:
        raise HostRejected('invalid_hostname')
    if _is_ip_literal(host) or _disguised_ip(host):
        raise HostRejected('ip_not_allowed')
    if host == 'localhost' and not settings.DEBUG:
        raise HostRejected('localhost_not_allowed')
    return host


def parse_extra_hosts(value) -> list[str]:
    if isinstance(value, str) or not isinstance(value, list):
        raise HostRejected('invalid')
    if len(value) > MAX_EXTRA_HOSTS:
        raise HostRejected('too_many')
    hosts: list[str] = []
    seen: set[str] = set()
    for item in value:
        host = classify_extra_host(item)
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    hosts.sort()
    return hosts


def host_from_application_url(url: str) -> str | None:
    """Hostname of an https origin, or None when the URL is not an allowed origin."""
    parsed = urlparse((url or '').strip())
    if (parsed.scheme or '').lower() != 'https':
        return None
    if parsed.username or parsed.password or parsed.port is not None:
        return None
    if parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        return None
    try:
        return classify_extra_host(parsed.hostname or '')
    except HostRejected:
        return None


def _profile(company_id: int) -> CompanyProfile | None:
    return CompanyProfile.objects.filter(company_id=company_id).first()


def _usable_stored(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    hosts: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        try:
            host = classify_extra_host(item)
        except HostRejected:
            continue
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    hosts.sort()
    return hosts


def platform_auth_link_hosts() -> list[str]:
    hosts: set[str] = set()
    for item in settings.EMAIL_AUTH_LINK_HOSTS:
        host = canonical_host_or_none(str(item))
        if host:
            hosts.add(host)
    if not settings.DEBUG:
        hosts = {host for host in hosts if host not in _LOOPBACK}
    return sorted(hosts)


def extra_auth_link_hosts(company_id: int) -> list[str]:
    profile = _profile(company_id)
    if profile is None:
        return []
    return _usable_stored(profile.auth_link_hosts)


def application_auth_link_hosts(company_id: int) -> list[str]:
    profile = _profile(company_id)
    if profile is None:
        return []
    return _usable_stored(profile.application_link_hosts)


def effective_auth_link_hosts(company_id: int | None) -> list[str]:
    """Hosts a company's auth mail may use. Unknown hosts are absent (fail closed)."""
    hosts = set(platform_auth_link_hosts())
    if company_id is not None:
        hosts.update(extra_auth_link_hosts(company_id))
        hosts.update(application_auth_link_hosts(company_id))
    if not settings.DEBUG:
        hosts = {host for host in hosts if host not in _LOOPBACK}
    return sorted(hosts)


def user_bearer_token(authorization: str) -> str | None:
    """User JWT from an Authorization header. Service keys are not forwarded."""
    value = (authorization or '').strip()
    if len(value) < 8 or value[:7].lower() != 'bearer ':
        return None
    token = value[7:].strip()
    if not token or token.startswith('esk_') or token.count('.') != 2:
        return None
    return token


def _store_application_hosts(company_id: int, hosts: list[str]) -> None:
    trimmed = hosts[:MAX_APPLICATION_HOSTS]
    profile, created = CompanyProfile.objects.get_or_create(
        company_id=company_id,
        defaults={'name': '', 'application_link_hosts': trimmed},
    )
    if created or list(profile.application_link_hosts or []) == trimmed:
        return
    profile.application_link_hosts = trimmed
    profile.save(update_fields=['application_link_hosts', 'updated_at'])


def refresh_application_hosts(company_id: int, authorization: str) -> None:
    """Replace stored application hosts from identity. Failures keep the stored list."""
    if user_bearer_token(authorization) is None:
        return
    base = (getattr(settings, 'IDENTITY_SERVICE_URL', '') or '').strip().rstrip('/')
    if not base:
        return
    try:
        response = requests.get(
            f'{base}/api/v1/oauth-redirects',
            params={'company_id': int(company_id)},
            headers={'Authorization': authorization.strip(), 'Accept': 'application/json'},
            timeout=(1, 2),
            allow_redirects=False,
        )
    except requests.RequestException:
        return
    if response.status_code != 200 or len(response.content) > _MAX_IDENTITY_BODY:
        return
    try:
        body = response.json()
    except ValueError:
        return
    if not isinstance(body, list):
        return
    hosts: set[str] = set()
    for row in body:
        if len(hosts) >= MAX_APPLICATION_HOSTS:
            break
        if not isinstance(row, dict) or row.get('is_active') is False:
            continue
        host = host_from_application_url(str(row.get('base_url') or ''))
        if host:
            hosts.add(host)
    _store_application_hosts(company_id, sorted(hosts))


def record_extra_host_changes(
    company_id: int,
    before: list[str],
    after: list[str],
    *,
    user_id: int | None,
) -> None:
    """Write one event-log row per added or removed extra hostname. The row holds the hostname only."""
    removed = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    rows = [(EVENT_REMOVED, host) for host in removed] + [(EVENT_ADDED, host) for host in added]
    for event_type, host in rows:
        EventLog.objects.create(
            company_id=company_id,
            user_id=user_id,
            event_type=event_type,
            data={'host': host},
        )


def _raw_host_list(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item.strip().lower() for item in raw if isinstance(item, str) and item.strip()]


def replace_extra_hosts(company_id: int, hosts: list[str], *, user_id: int | None) -> CompanyProfile:
    with transaction.atomic():
        profile = CompanyProfile.objects.select_for_update().filter(company_id=company_id).first()
        if profile is None:
            profile = CompanyProfile(company_id=company_id, name='', auth_link_hosts=hosts)
            try:
                with transaction.atomic():
                    profile.save()
            except IntegrityError:
                profile = CompanyProfile.objects.select_for_update().get(company_id=company_id)
            else:
                record_extra_host_changes(company_id, [], hosts, user_id=user_id)
                return profile
        before = _raw_host_list(profile.auth_link_hosts)
        profile.auth_link_hosts = hosts
        profile.save(update_fields=['auth_link_hosts', 'updated_at'])
        record_extra_host_changes(company_id, before, hosts, user_id=user_id)
    return profile


def auth_link_hosts_payload(company_id: int) -> dict:
    return {
        'company_id': int(company_id),
        'hosts': extra_auth_link_hosts(company_id),
        'application_hosts': application_auth_link_hosts(company_id),
        'platform_hosts': platform_auth_link_hosts(),
        'auth_link_hosts': effective_auth_link_hosts(company_id),
    }


def literal_https_host_allowed(href: str, allowed: set[str]) -> bool:
    parsed = urlparse((href or '').strip())
    if (parsed.scheme or '').lower() != 'https':
        return False
    if parsed.username or parsed.password or parsed.port is not None:
        return False
    host = canonical_host_or_none(parsed.hostname or '')
    return bool(host) and host in allowed
