"""Identity-service MCP internal APIs (revocation list, approvals, call logs)."""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger(__name__)


class McpIdentityUnavailable(Exception):
    """Identity MCP internal API could not be reached or rejected the call."""


class McpAgentCompanyMismatch(Exception):
    """Agent JWT company_id does not match Identity revocation metadata."""


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
    simple = getattr(settings, 'SIMPLE_JWT', None) or {}
    signing_key = simple.get('SIGNING_KEY')
    if not signing_key:
        from rest_framework_simplejwt.settings import api_settings

        signing_key = getattr(api_settings, 'SIGNING_KEY', None)
    return bool(signing_key)


def _require_service_assertions() -> None:
    if not _signing_key_configured():
        raise McpIdentityUnavailable('MCP service assertions are not configured.')
    if not identity_origin():
        raise McpIdentityUnavailable('Identity issuer is not configured.')


def verify_agent_company_binding(agent_id: str, company_id: int) -> None:
    """When Identity publishes ``agents[].company_id``, it must match the access token."""
    if not _revocation_snapshot['trusted']:
        raise McpIdentityUnavailable('Revocation list is unavailable.')
    agent_id = str(agent_id or '')
    for row in _revocation_snapshot.get('agents') or []:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get('agent_id') or row.get('id') or '')
        if row_id != agent_id:
            continue
        raw_company = row.get('company_id')
        if raw_company in (None, ''):
            return
        if int(raw_company) != int(company_id):
            raise McpAgentCompanyMismatch('Agent company binding mismatch.')
        return


def _revocation_assertion_headers() -> dict[str, str]:
    _require_service_assertions()
    origin = identity_origin()
    assertion = build_mcp_revocation_assertion(audience_url=origin)
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
        raise McpIdentityUnavailable('Revocation list is unavailable.')
    return agent_id in _revocation_snapshot['ids']


def _refresh_revocations() -> None:
    base = identity_api_base()
    if not base:
        _revocation_snapshot['trusted'] = False
        raise McpIdentityUnavailable('Identity service URL is not configured.')
    url = f'{base}/api/v1/mcp/internal/mcp-revocations'
    try:
        response = requests.get(
            url,
            headers=_revocation_assertion_headers(),
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            _revocation_snapshot['trusted'] = False
            raise McpIdentityUnavailable('Revocation list endpoint missing.')
        response.raise_for_status()
        payload = response.json()
        issuer = identity_origin()
        if not issuer:
            _revocation_snapshot['trusted'] = False
            raise McpIdentityUnavailable('Identity issuer is not configured.')
        jwks = getattr(settings, 'IDENTITY_JWKS_DOCUMENT', None)
        ids, cache_seconds, agents = parse_trusted_revocation_poll_response(
            payload,
            expected_issuer=issuer,
            jwks=jwks,
        )
    except McpIdentityUnavailable:
        raise
    except InternalAssertionError as exc:
        _revocation_snapshot['trusted'] = False
        logger.warning('MCP revocation list untrusted (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('Revocation list payload invalid.') from exc
    except (requests.RequestException, OSError) as exc:
        _revocation_snapshot['trusted'] = False
        logger.warning('MCP revocation list fetch failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('Revocation list fetch failed.') from exc
    _revocation_snapshot['ids'] = ids
    _revocation_snapshot['agents'] = list(agents)
    _revocation_snapshot['cache_seconds'] = min(int(cache_seconds), REVOCATION_CACHE_SECONDS)
    _revocation_snapshot['fetched_at'] = time.monotonic()
    _revocation_snapshot['trusted'] = True


def _approval_assertion(payload: dict[str, Any]) -> str:
    origin = identity_origin()
    return build_mcp_approval_assertion(
        company_id=int(payload['company_id']),
        agent_id=str(payload['agent_id']),
        service=str(payload.get('service') or 'email'),
        action=str(payload.get('action') or ''),
        arguments_hash=str(payload.get('arguments_hash') or ''),
        audience_url=origin,
    )


def resolve_approval_state(payload: dict[str, Any]) -> str | None:
    """Ask Identity for pending, approved, expired (consumed), or denied state."""
    _require_service_assertions()
    base = identity_api_base()
    if not base:
        raise McpIdentityUnavailable('Identity service URL is not configured.')
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
        raise McpIdentityUnavailable('Approval state lookup failed.') from exc


def consume_approval(approval_id: str, *, payload: dict[str, Any] | None = None) -> bool:
    _require_service_assertions()
    base = identity_api_base()
    if not base or not approval_id:
        raise McpIdentityUnavailable('Identity service URL is not configured.')
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
            raise McpIdentityUnavailable('Approval consume endpoint missing.')
        response.raise_for_status()
        return bool(response.json().get('consumed'))
    except McpIdentityUnavailable:
        raise
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP approval consume failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('Approval consume failed.') from exc


def create_approval_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    _require_service_assertions()
    origin = identity_origin()
    base = identity_api_base()
    if not origin or not base:
        raise McpIdentityUnavailable('Identity service URL is not configured.')
    args_hash = str(payload.get('arguments_hash') or '')
    assertion = build_mcp_approval_assertion(
        company_id=int(payload['company_id']),
        agent_id=str(payload['agent_id']),
        service=str(payload.get('service') or 'email'),
        action=str(payload.get('action') or ''),
        arguments_hash=args_hash,
        audience_url=origin,
    )
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
            raise McpIdentityUnavailable('Approval create endpoint missing.')
        response.raise_for_status()
        data = response.json()
        return {'id': str(data.get('id') or '')}
    except McpIdentityUnavailable:
        raise
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP approval request failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('Approval request failed.') from exc


def forward_call_log(entry: dict[str, Any]) -> None:
    try:
        _require_service_assertions()
    except McpIdentityUnavailable:
        return
    origin = identity_origin()
    base = identity_api_base()
    if not origin or not base:
        return
    assertion = build_mcp_call_log_assertion(audience_url=origin)
    url = f'{base}/api/v1/mcp/internal/mcp-call-logs'
    body = {
        'agent_id': entry.get('agent_id'),
        'company_id': entry.get('company_id'),
        'service': entry.get('service'),
        'tool': entry.get('tool'),
        'method': entry.get('method'),
        'path': entry.get('path'),
        'status': entry.get('status'),
        'arguments': entry.get('arguments') or {},
    }
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
