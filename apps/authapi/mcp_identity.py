"""Identity-service MCP internal APIs (revocation list, approvals, call logs)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from django.conf import settings
from shellui_mcp.internal import (
    REVOCATION_CACHE_SECONDS,
    build_mcp_approval_assertion,
    build_mcp_call_log_assertion,
)

logger = logging.getLogger(__name__)

_revocation_snapshot: dict[str, Any] = {'fetched_at': 0.0, 'ids': frozenset()}


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


def _assertions_enabled() -> bool:
    simple = getattr(settings, 'SIMPLE_JWT', None) or {}
    signing_key = simple.get('SIGNING_KEY')
    if not signing_key:
        from rest_framework_simplejwt.settings import api_settings

        signing_key = getattr(api_settings, 'SIGNING_KEY', None)
    return bool(signing_key and identity_origin())


def agent_is_revoked(agent_id: str) -> bool:
    agent_id = str(agent_id or '')
    if not agent_id:
        return True
    now = time.monotonic()
    if now - float(_revocation_snapshot['fetched_at']) >= REVOCATION_CACHE_SECONDS:
        _refresh_revocations()
    return agent_id in _revocation_snapshot['ids']


def _service_assertion_headers() -> dict[str, str]:
    if not _assertions_enabled():
        return {}
    origin = identity_origin()
    if not origin:
        return {}
    assertion = build_mcp_call_log_assertion(audience_url=origin)
    return {'Authorization': f'Bearer {assertion}'}


def _refresh_revocations() -> None:
    base = identity_api_base()
    if not base:
        return
    url = f'{base}/api/v1/mcp/internal/mcp-revocations'
    headers = _service_assertion_headers()
    try:
        response = requests.get(url, headers=headers, timeout=getattr(settings, 'JWKS_TIMEOUT', 15))
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning('MCP revocation list fetch failed (%s).', type(exc).__name__)
        return
    ids = payload.get('revoked_agent_ids') or []
    _revocation_snapshot['ids'] = frozenset(str(item) for item in ids)
    _revocation_snapshot['fetched_at'] = time.monotonic()


def resolve_approval_state(payload: dict[str, Any]) -> str | None:
    """Ask Identity for pending, approved, or executed state (internal API)."""
    base = identity_api_base()
    if not base or not _assertions_enabled():
        return None
    origin = identity_origin()
    if not origin:
        return None
    assertion = build_mcp_approval_assertion(
        company_id=int(payload['company_id']),
        agent_id=str(payload['agent_id']),
        service=str(payload.get('service') or 'email'),
        action=str(payload.get('action') or ''),
        arguments_hash=str(payload.get('arguments_hash') or ''),
        audience_url=origin,
    )
    url = f'{base}/api/v1/mcp/internal/mcp-approval-state'
    try:
        response = requests.post(
            url,
            json={
                'service': payload.get('service'),
                'action': payload.get('action'),
                'arguments_hash': payload.get('arguments_hash'),
            },
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        state = str(response.json().get('status') or '')
        if state == 'approved':
            payload['approval_id'] = str(response.json().get('id') or '')
        return state or None
    except requests.RequestException as exc:
        logger.debug('MCP approval state lookup failed (%s).', type(exc).__name__)
        return None


def consume_approval(approval_id: str) -> bool:
    base = identity_api_base()
    if not base or not _assertions_enabled() or not approval_id:
        return False
    origin = identity_origin()
    if not origin:
        return False
    assertion = build_mcp_call_log_assertion(audience_url=origin)
    url = f'{base}/api/v1/mcp/internal/mcp-approval-consume'
    try:
        response = requests.post(
            url,
            json={'id': approval_id},
            headers={'Authorization': f'Bearer {assertion}'},
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return bool(response.json().get('consumed'))
    except requests.RequestException as exc:
        logger.debug('MCP approval consume failed (%s).', type(exc).__name__)
        return False


def create_approval_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _assertions_enabled():
        return None
    origin = identity_origin()
    base = identity_api_base()
    if not origin or not base:
        return None
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
        response.raise_for_status()
        data = response.json()
        return {'id': str(data.get('id') or '')}
    except requests.RequestException as exc:
        logger.warning('MCP approval request failed (%s).', type(exc).__name__)
        return None


def forward_call_log(entry: dict[str, Any]) -> None:
    if not _assertions_enabled():
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
