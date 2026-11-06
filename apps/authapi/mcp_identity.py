"""Identity-service MCP internal APIs (revocation list, approvals, call logs)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import requests
from django.conf import settings
from shellui_django.mcp.internal import (
    InternalAssertionError,
    REVOCATION_CACHE_SECONDS,
    build_mcp_approval_assertion,
    build_mcp_call_log_assertion,
    build_mcp_revocation_assertion,
    parse_trusted_revocation_poll_response,
)

from config.service_signing import signing_kwargs

logger = logging.getLogger(__name__)


class McpIdentityUnavailable(Exception):
    """Identity internal MCP call failed; ``args[0]`` is a stable machine code (not user copy)."""


class McpAgentCompanyMismatch(Exception):
    """Token does not match signed revocation authority; ``args[0]`` is a stable machine code."""


_revocation_snapshot: dict[str, Any] = {
    'fetched_at': 0.0,
    'ids': frozenset(),
    'agents': [],
    'cache_seconds': REVOCATION_CACHE_SECONDS,
    'trusted': False,
}


def reset_revocation_cache_for_tests() -> None:
    _revocation_snapshot['fetched_at'] = 0.0
    _revocation_snapshot['ids'] = frozenset()
    _revocation_snapshot['agents'] = []
    _revocation_snapshot['cache_seconds'] = REVOCATION_CACHE_SECONDS
    _revocation_snapshot['trusted'] = False


def identity_origin() -> str:
    issuer = (getattr(settings, 'IDENTITY_ISSUER', None) or '').strip().rstrip('/')
    if issuer:
        return issuer
    base = (getattr(settings, 'IDENTITY_SERVICE_URL', None) or '').strip().rstrip('/')
    return base


def identity_api_base() -> str:
    base = (getattr(settings, 'IDENTITY_SERVICE_URL', None) or '').strip().rstrip('/')
    if not base:
        base = identity_origin()
    return base.rstrip('/')


def _signing_key_configured() -> bool:
    """This service's own RS256 key and issuer for internal MCP assertions (config/service_signing.py)."""
    return bool(getattr(settings, 'MCP_SIGNING_KEY_PEM', '') and getattr(settings, 'MCP_SIGNING_ISSUER', ''))


def _require_service_assertions() -> None:
    if not _signing_key_configured():
        raise McpIdentityUnavailable('mcp_assertions_not_configured')
    if not identity_origin():
        raise McpIdentityUnavailable('identity_issuer_not_configured')


def verify_agent_company_binding(
    agent_id: str,
    company_id: int,
    *,
    claims: dict[str, Any] | None = None,
) -> None:
    """When Identity publishes a row for this agent, regional and global authority must match the token."""
    if not _revocation_snapshot['trusted']:
        raise McpIdentityUnavailable('revocation_list_unavailable')
    agent_id = str(agent_id or '')
    claims = claims or {}
    token_global = claims.get('global_company_id')
    token_binding = claims.get('company_binding_version')
    token_grant = claims.get('grant_version')
    token_has_global = token_global is not None
    for row in _revocation_snapshot.get('agents') or []:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get('agent_id') or row.get('id') or '')
        if row_id != agent_id:
            continue
        g = row.get('global_company_id', None)
        b = row.get('company_binding_version', None)
        row_has_global = g is not None and b is not None
        if row_has_global:
            if not token_has_global or token_grant is None or token_binding is None:
                raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
            if str(token_global or '') != str(g):
                raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
            if int(token_binding) != int(b):
                raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
            gv_row = row.get('grant_version')
            if gv_row is None or int(token_grant) != int(gv_row):
                raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        else:
            if token_has_global:
                raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        raw_company = row.get('company_id')
        if raw_company not in (None, ''):
            if int(raw_company) != int(company_id):
                raise McpAgentCompanyMismatch('regional_company_mismatch')
        return
    return


def _revocation_assertion_headers() -> dict[str, str]:
    _require_service_assertions()
    origin = identity_origin()
    assertion = build_mcp_revocation_assertion(audience_url=origin, **signing_kwargs())
    return {'Authorization': f'Bearer {assertion}'}


def ensure_revocation_snapshot() -> None:
    """Refresh revocation list when stale. Raises when Identity cannot be trusted."""
    now = time.monotonic()
    ttl = float(_revocation_snapshot.get('cache_seconds') or REVOCATION_CACHE_SECONDS)
    if (
        _revocation_snapshot['trusted']
        and now - float(_revocation_snapshot['fetched_at']) < ttl
    ):
        return
    _refresh_revocations()


def agent_is_revoked(agent_id: str) -> bool:
    agent_id = str(agent_id or '')
    if not agent_id:
        return True
    if not _revocation_snapshot['trusted']:
        raise McpIdentityUnavailable('revocation_list_unavailable')
    return agent_id in _revocation_snapshot['ids']


def _refresh_revocations() -> None:
    base = identity_api_base()
    if not base:
        _revocation_snapshot['trusted'] = False
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    url = f'{base}/api/v1/mcp/internal/mcp-revocations'
    try:
        response = requests.get(
            url,
            headers=_revocation_assertion_headers(),
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            _revocation_snapshot['trusted'] = False
            raise McpIdentityUnavailable('revocation_list_endpoint_missing')
        response.raise_for_status()
        payload = response.json()
        issuer = identity_origin()
        if not issuer:
            _revocation_snapshot['trusted'] = False
            raise McpIdentityUnavailable('identity_issuer_not_configured')
        jwks = getattr(settings, 'IDENTITY_JWKS_DOCUMENT', None)
        ids, cache_seconds, agents = parse_trusted_revocation_poll_response(
            payload,
            expected_issuer=issuer,
            # Without a pinned document, fetch keys from IDENTITY_JWKS_URL, not the public issuer.
            identity_jwks_url=None if jwks is not None else (getattr(settings, 'IDENTITY_JWKS_URL', None) or None),
            jwks=jwks,
        )
    except McpIdentityUnavailable:
        raise
    except InternalAssertionError as exc:
        _revocation_snapshot['trusted'] = False
        logger.warning('MCP revocation list untrusted (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('revocation_list_payload_invalid') from exc
    except (requests.RequestException, OSError) as exc:
        _revocation_snapshot['trusted'] = False
        logger.warning('MCP revocation list fetch failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('revocation_list_fetch_failed') from exc
    _revocation_snapshot['ids'] = ids
    _revocation_snapshot['agents'] = list(agents)
    _revocation_snapshot['cache_seconds'] = min(int(cache_seconds), REVOCATION_CACHE_SECONDS)
    _revocation_snapshot['fetched_at'] = time.monotonic()
    _revocation_snapshot['trusted'] = True


def _approval_assertion_extra_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Extra kwargs for ``build_mcp_approval_assertion`` (django-commons #14): strict presence, never default 0."""
    raw_global = payload.get('global_company_id', None)
    has_global = raw_global is not None
    has_binding = payload.get('company_binding_version', None) is not None
    has_grant = 'grant_version' in payload and payload['grant_version'] is not None
    extra: dict[str, Any] = {}
    if has_global or has_binding:
        if not (has_global and has_binding and has_grant):
            raise McpIdentityUnavailable('approval_assertion_delegation_incomplete')
        try:
            extra['global_company_id'] = str(uuid.UUID(str(raw_global)))
        except (ValueError, TypeError) as exc:
            raise McpIdentityUnavailable('approval_assertion_global_company_id_invalid') from exc
        extra['company_binding_version'] = int(payload['company_binding_version'])
        extra['grant_version'] = int(payload['grant_version'])
        return extra
    if has_grant:
        extra['grant_version'] = int(payload['grant_version'])
    return extra


def _approval_assertion_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Keyword args for ``build_mcp_approval_assertion`` aligned with identity #135 / django-commons #14."""
    origin = identity_origin()
    return {
        'company_id': int(payload['company_id']),
        'agent_id': str(payload['agent_id']),
        'service': str(payload.get('service') or 'email'),
        'action': str(payload.get('action') or ''),
        'arguments_hash': str(payload.get('arguments_hash') or ''),
        'audience_url': origin,
        **signing_kwargs(),
        **_approval_assertion_extra_kwargs(payload),
    }


def _approval_assertion(payload: dict[str, Any]) -> str:
    return build_mcp_approval_assertion(**_approval_assertion_kwargs(payload))


def resolve_approval_state(payload: dict[str, Any]) -> str | None:
    """Ask Identity for pending, approved, expired (consumed), or denied state."""
    _require_service_assertions()
    base = identity_api_base()
    if not base:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    assertion = _approval_assertion(payload)
    url = f'{base}/api/v1/mcp/internal/mcp-approval-state'
    try:
        response = requests.post(
            url,
            json={},
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        state = str(body.get('status') or '')
        if state == 'expired':
            return 'executed'
        if state == 'approved':
            payload['approval_id'] = str(body.get('id') or body.get('approval_id') or '')
            return 'approved'
        if state in {'pending', 'denied'}:
            return state
        return state or None
    except McpIdentityUnavailable:
        raise
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP approval state lookup failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('approval_state_lookup_failed') from exc


def consume_approval(approval_id: str, *, payload: dict[str, Any] | None = None) -> bool:
    _require_service_assertions()
    base = identity_api_base()
    if not base or not approval_id:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    context = payload or {}
    assertion = _approval_assertion(context)
    url = f'{base}/api/v1/mcp/internal/mcp-approval-consume'
    try:
        response = requests.post(
            url,
            json={'approval_id': approval_id},
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            raise McpIdentityUnavailable('approval_consume_endpoint_missing')
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            raise McpIdentityUnavailable('approval_consume_response_invalid')
        if body.get('consumed') is not True:
            raise McpIdentityUnavailable('approval_consume_not_true')
        return True
    except McpIdentityUnavailable:
        raise
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP approval consume failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('approval_consume_failed') from exc


def create_approval_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    _require_service_assertions()
    origin = identity_origin()
    base = identity_api_base()
    if not origin or not base:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    assertion = build_mcp_approval_assertion(**_approval_assertion_kwargs(payload))
    body = {
        'arguments': payload.get('arguments') or {},
    }
    url = f'{base}/api/v1/mcp/internal/mcp-approval-requests'
    try:
        response = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            raise McpIdentityUnavailable('approval_create_endpoint_missing')
        response.raise_for_status()
        data = response.json()
        return {'id': str(data.get('id') or '')}
    except McpIdentityUnavailable:
        raise
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP approval request failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('approval_request_failed') from exc


def forward_call_log(entry: dict[str, Any]) -> None:
    try:
        _require_service_assertions()
    except McpIdentityUnavailable:
        return
    origin = identity_origin()
    base = identity_api_base()
    if not origin or not base:
        return
    assertion = build_mcp_call_log_assertion(audience_url=origin, **signing_kwargs())
    url = f'{base}/api/v1/mcp/internal/mcp-call-logs'
    body: dict[str, Any] = {
        'agent_id': entry.get('agent_id'),
        'company_id': entry.get('company_id'),
        'service': entry.get('service'),
        'tool': entry.get('tool'),
        'method': entry.get('method'),
        'resource_path': entry.get('resource_path'),
        'access_kind': entry.get('access_kind'),
        'outcome': entry.get('outcome'),
        'duration_ms': entry.get('duration_ms'),
    }
    if entry.get('operation_title'):
        body['operation_title'] = entry.get('operation_title')
    if entry.get('http_status') is not None:
        body['http_status'] = int(entry['http_status'])
    if entry.get('error_code'):
        body['error_code'] = str(entry['error_code'])
    gv = entry.get('grant_version')
    if gv is not None:
        body['grant_version'] = int(gv)
    if entry.get('global_company_id') is not None:
        body['global_company_id'] = str(entry['global_company_id'])
    if entry.get('company_binding_version') is not None:
        body['company_binding_version'] = int(entry['company_binding_version'])
    cid = entry.get('company_id')
    if cid is not None:
        try:
            from apps.email.models import CompanyProfile

            profile = CompanyProfile.objects.filter(company_id=int(cid)).only('name').first()
            if profile and (profile.name or '').strip():
                body['company_name'] = str(profile.name).strip()
        except (TypeError, ValueError, OSError):
            pass
    try:
        response = requests.post(
            url,
            json=body,
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning('MCP call log forward failed (%s).', type(exc).__name__)
