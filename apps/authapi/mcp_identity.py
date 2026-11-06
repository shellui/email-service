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

from config.service_signing import build_mcp_delegated_authority_assertion, signing_kwargs

from apps.authapi.mcp_claims import AgentClaimsValidationError, strict_json_nonneg_int

logger = logging.getLogger(__name__)

# Filled by ``resolve_approval_state`` on success so the MCP server can log ``approval_request_id``.
_last_approval_state_extras: dict[str, Any] = {}


def last_approval_state_extras() -> dict[str, Any]:
    return dict(_last_approval_state_extras)


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


def _strict_snapshot_uuid(raw: Any, *, field: str) -> str | None:
    if raw in (None, ''):
        return None
    try:
        u = uuid.UUID(str(raw))
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
    if u.int == 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    return str(u)


def _strict_snapshot_int(raw: Any, *, field: str) -> int:
    """Revocation snapshot integers must be JSON integers in the live row (no str/float/bool coercion)."""
    if type(raw) is not int:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    return raw


def _parse_live_delegated_authority_body(body: Any, *, agent_id: str) -> dict[str, Any]:
    """
    Strict JSON body from Identity live delegated-authority endpoint (POST).

    Returns canonical ``agent_id``, ``company_id``, ``global_company_id``, ``company_binding_version``,
    ``grant_version`` with JSON int types only for numeric fields (``0`` is valid).
    """
    if not isinstance(body, dict):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    row = body.get('agent') if isinstance(body.get('agent'), dict) else body
    if not isinstance(row, dict):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    rid = str(row.get('agent_id') or row.get('id') or '')
    if not rid or rid != str(agent_id):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    cid_raw = row.get('company_id')
    if type(cid_raw) is not int or isinstance(cid_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if cid_raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    g_raw = row.get('global_company_id', None)
    try:
        ug = uuid.UUID(str(g_raw))
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
    if ug.int == 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    cbv_raw = row.get('company_binding_version')
    gv_raw = row.get('grant_version')
    if type(cbv_raw) is not int or isinstance(cbv_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if cbv_raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if type(gv_raw) is not int or isinstance(gv_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if gv_raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    # Identity may signal non-authoritative rows (revoked, paused, stale binding) with explicit flags.
    for flag in ('revoked', 'paused', 'grant_disabled', 'binding_stale'):
        v = row.get(flag)
        if v is True:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if v is not None and v is not False:
            raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    return {
        'agent_id': rid,
        'company_id': cid_raw,
        'global_company_id': str(ug),
        'company_binding_version': cbv_raw,
        'grant_version': gv_raw,
    }


def _delegated_authority_assertion_headers() -> dict[str, str]:
    _require_service_assertions()
    origin = identity_origin()
    assertion = build_mcp_delegated_authority_assertion(audience_url=origin)
    return {'Authorization': f'Bearer {assertion}', 'Content-Type': 'application/json'}


def _fetch_live_delegated_authority(agent_id: str) -> dict[str, Any]:
    """
    Current delegated-agent row from Identity (not only the signed revocation snapshot).

    Uses a dedicated service JWT (``aud`` ``shellui-mcp-delegated-authority``). Fails closed on transport
    or malformed JSON.
    """
    _require_service_assertions()
    base = identity_api_base()
    if not base:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    url = f'{base}/api/v1/mcp/internal/mcp-delegated-agent-authority'
    try:
        response = requests.post(
            url,
            json={'agent_id': str(agent_id)},
            headers=_delegated_authority_assertion_headers(),
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP delegated authority fetch failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('delegated_authority_fetch_failed') from exc
    if response.status_code == 404:
        raise McpAgentCompanyMismatch('authority_row_missing')
    if response.status_code >= 500:
        raise McpIdentityUnavailable('delegated_authority_fetch_failed')
    if response.status_code != 200:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    try:
        payload = response.json()
    except ValueError as exc:
        raise McpIdentityUnavailable('delegated_authority_response_invalid') from exc
    return _parse_live_delegated_authority_body(payload, agent_id=str(agent_id))


def _claims_require_live_delegated(claims: dict[str, Any]) -> bool:
    return (
        claims.get('global_company_id') is not None
        and claims.get('company_binding_version') is not None
        and claims.get('grant_version') is not None
    )


def verify_live_delegated_authority_against_token(agent_id: str, company_id: int, claims: dict[str, Any]) -> None:
    """
    Re-check Identity live delegated row (used before each approval-related Identity call).

    Fails closed when the JWT triple does not match Identity or the agent is not delegated.
    """
    if not _claims_require_live_delegated(claims):
        return
    live = _fetch_live_delegated_authority(str(agent_id))
    if int(live['company_id']) != int(company_id):
        raise McpAgentCompanyMismatch('regional_company_mismatch')
    if str(live['global_company_id']) != str(claims.get('global_company_id')):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if int(live['company_binding_version']) != int(claims['company_binding_version']):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if int(live['grant_version']) != int(claims['grant_version']):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')


def verify_agent_company_binding(
    agent_id: str,
    company_id: int,
    *,
    claims: dict[str, Any] | None = None,
) -> None:
    """
    Bind JWT to the signed revocation snapshot (fail closed).

    - Exactly one row for this ``agent_id`` must exist; if absent, deny (no silent allow).
    - Multiple rows for the same agent id: ambiguous, deny.
    - Token must match regional ``company_id`` and delegated triple when the row is global.
    - For delegated (global) rows, a live Identity service-only row must match the same triple
      (snapshot agreement alone is not sufficient).
    """
    if not _revocation_snapshot['trusted']:
        raise McpIdentityUnavailable('revocation_list_unavailable')
    agent_id = str(agent_id or '')
    claims = claims or {}
    token_global = claims.get('global_company_id')
    token_binding = claims.get('company_binding_version')
    token_grant = claims.get('grant_version')
    token_has_global = token_global is not None

    rows = [r for r in (_revocation_snapshot.get('agents') or []) if isinstance(r, dict)]
    matching = [r for r in rows if str(r.get('agent_id') or r.get('id') or '') == agent_id]
    if len(matching) > 1:
        raise McpAgentCompanyMismatch('authority_ambiguous')
    if len(matching) != 1:
        raise McpAgentCompanyMismatch('authority_row_missing')

    row = matching[0]
    if row.get('mcp_authority_snapshot_rejected') is True:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    g_raw = row.get('global_company_id', None)
    b_raw = row.get('company_binding_version', None)
    row_gci = _strict_snapshot_uuid(g_raw, field='global_company_id')
    row_cbv: int | None
    if b_raw in (None, ''):
        row_cbv = None
    else:
        row_cbv = _strict_snapshot_int(b_raw, field='company_binding_version')
    row_has_global = row_gci is not None and row_cbv is not None

    raw_company = row.get('company_id')
    row_company: int | None = None
    if raw_company not in (None, ''):
        row_company = _strict_snapshot_int(raw_company, field='company_id')

    if row_has_global:
        gv_row_raw = row.get('grant_version')
        if gv_row_raw in (None, ''):
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        gv_row = _strict_snapshot_int(gv_row_raw, field='grant_version')
        if not token_has_global or token_grant is None or token_binding is None:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if str(token_global) != row_gci:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if type(token_binding) is not int or token_binding != row_cbv:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if type(token_grant) is not int or token_grant != gv_row:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if row_company is not None and row_company != int(company_id):
            raise McpAgentCompanyMismatch('regional_company_mismatch')
        try:
            verify_live_delegated_authority_against_token(
                agent_id,
                int(company_id),
                {
                    'global_company_id': token_global,
                    'company_binding_version': token_binding,
                    'grant_version': token_grant,
                },
            )
        except McpIdentityUnavailable:
            raise
        except McpAgentCompanyMismatch:
            raise
    else:
        if token_has_global:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if row_company is None:
            raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
        if row_company != int(company_id):
            raise McpAgentCompanyMismatch('regional_company_mismatch')


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


def _strict_assertion_nonneg_int(raw: Any, *, field: str) -> int:
    if type(raw) is not int or isinstance(raw, bool):
        raise McpIdentityUnavailable('approval_assertion_invalid_int')
    if raw < 0:
        raise McpIdentityUnavailable('approval_assertion_invalid_int')
    return raw


def _approval_assertion_extra_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Strict delegation triple for service assertions (no bool coercion; reject unknown keys)."""
    allowed_payload_keys = {
        'agent_id',
        'company_id',
        'service',
        'action',
        'arguments_hash',
        'path',
        'method',
        'grant_version',
        'global_company_id',
        'company_binding_version',
        'approval_id',
    }
    unknown = set(payload.keys()) - allowed_payload_keys
    if unknown:
        raise McpIdentityUnavailable('approval_assertion_payload_unknown_keys')

    raw_global = payload.get('global_company_id', None)
    has_global = raw_global is not None
    raw_binding = payload.get('company_binding_version', None)
    has_binding = raw_binding is not None
    raw_gv = payload.get('grant_version', None)
    has_grant = raw_gv is not None

    if has_global or has_binding:
        if not (has_global and has_binding and has_grant):
            raise McpIdentityUnavailable('approval_assertion_delegation_incomplete')
        try:
            ug = uuid.UUID(str(raw_global))
        except (ValueError, TypeError) as exc:
            raise McpIdentityUnavailable('approval_assertion_global_company_id_invalid') from exc
        if ug.int == 0:
            raise McpIdentityUnavailable('approval_assertion_global_company_id_invalid')
        delegated: dict[str, Any] = {'global_company_id': str(ug)}
        delegated['company_binding_version'] = _strict_assertion_nonneg_int(
            payload['company_binding_version'], field='company_binding_version'
        )
        delegated['grant_version'] = _strict_assertion_nonneg_int(payload['grant_version'], field='grant_version')
        return delegated
    return {}


def _approval_assertion_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Keyword args for ``build_mcp_approval_assertion``."""
    origin = identity_origin()
    cid_raw = payload['company_id']
    if type(cid_raw) is not int or isinstance(cid_raw, bool):
        raise McpIdentityUnavailable('approval_assertion_company_id_invalid')
    company_id = cid_raw
    if company_id < 0:
        raise McpIdentityUnavailable('approval_assertion_company_id_invalid')
    aid = str(payload.get('agent_id') or '')
    if not aid:
        raise McpIdentityUnavailable('approval_assertion_agent_id_invalid')
    delegated = _approval_assertion_extra_kwargs(payload)
    base: dict[str, Any] = {
        'company_id': company_id,
        'agent_id': aid,
        'service': str(payload.get('service') or 'email'),
        'action': str(payload.get('action') or ''),
        'arguments_hash': str(payload.get('arguments_hash') or ''),
        'audience_url': origin,
        **signing_kwargs(),
    }
    if delegated:
        base.update(delegated)
    elif 'grant_version' in payload and payload['grant_version'] is not None:
        base['grant_version'] = _strict_assertion_nonneg_int(payload['grant_version'], field='grant_version')
    return base


def _approval_assertion(payload: dict[str, Any]) -> str:
    return build_mcp_approval_assertion(**_approval_assertion_kwargs(payload))


def _maybe_verify_live_delegated_for_approval(payload: dict[str, Any]) -> None:
    if payload.get('global_company_id') is None:
        return
    verify_live_delegated_authority_against_token(
        str(payload.get('agent_id') or ''),
        int(payload['company_id']),
        {
            'global_company_id': payload.get('global_company_id'),
            'company_binding_version': payload.get('company_binding_version'),
            'grant_version': payload.get('grant_version'),
        },
    )


def resolve_approval_state(payload: dict[str, Any]) -> str | None:
    """Ask Identity for pending, approved, expired (consumed), or denied state."""
    global _last_approval_state_extras
    _last_approval_state_extras = {}
    _require_service_assertions()
    base = identity_api_base()
    if not base:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    _maybe_verify_live_delegated_for_approval(payload)
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
            return 'not_found'
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            raise McpIdentityUnavailable('approval_state_response_invalid') from None
        state = str(body.get('status') or '').strip().lower()
        rid = body.get('approval_request_id') or body.get('id') or body.get('approval_id')
        if rid:
            try:
                _last_approval_state_extras['approval_request_id'] = str(uuid.UUID(str(rid)))
            except (ValueError, TypeError):
                _last_approval_state_extras['approval_request_id'] = str(rid)
        if not state:
            return 'invalid'
        if state == 'expired':
            return 'executed'
        if state == 'approved':
            aid = body.get('id') or body.get('approval_id')
            if not aid:
                return 'invalid'
            payload['approval_id'] = str(aid)
            return 'approved'
        if state in {'pending', 'denied'}:
            return state
        return 'unknown'
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
    _maybe_verify_live_delegated_for_approval(context)
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
    _maybe_verify_live_delegated_for_approval(payload)
    assertion = build_mcp_approval_assertion(**_approval_assertion_kwargs(payload))
    body: dict[str, Any] = {}
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
        rid = data.get('approval_request_id') or data.get('id')
        out: dict[str, Any] = {}
        if rid:
            try:
                out['id'] = str(uuid.UUID(str(rid)))
            except (ValueError, TypeError):
                out['id'] = str(rid)
        return out if out.get('id') else None
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
    cc_raw = entry.get('company_id')
    if cc_raw is None:
        return
    try:
        cc_val = strict_json_nonneg_int(cc_raw, field='company_id')
    except AgentClaimsValidationError:
        return
    gv_for_live = entry.get('grant_version')
    cbv_for_live = entry.get('company_binding_version')
    if (
        entry.get('global_company_id') is not None
        and cbv_for_live is not None
        and gv_for_live is not None
        and entry.get('agent_id') is not None
    ):
        try:
            gv_i = strict_json_nonneg_int(gv_for_live, field='grant_version')
            cbv_i = strict_json_nonneg_int(cbv_for_live, field='company_binding_version')
        except AgentClaimsValidationError:
            return
        try:
            verify_live_delegated_authority_against_token(
                str(entry['agent_id']),
                cc_val,
                {
                    'global_company_id': entry.get('global_company_id'),
                    'company_binding_version': cbv_i,
                    'grant_version': gv_i,
                },
            )
        except (McpIdentityUnavailable, McpAgentCompanyMismatch):
            return
    assertion = build_mcp_call_log_assertion(audience_url=origin, **signing_kwargs())
    url = f'{base}/api/v1/mcp/internal/mcp-call-logs'
    meth = entry.get('method')
    stat = entry.get('http_status')
    dur_raw = entry.get('duration_ms')
    try:
        dur_val = strict_json_nonneg_int(dur_raw, field='duration_ms')
    except AgentClaimsValidationError:
        return
    body: dict[str, Any] = {
        'agent_id': entry.get('agent_id'),
        'company_id': cc_val,
        'service': entry.get('service'),
        'tool': entry.get('tool'),
        'method': meth,
        'http_method': meth,
        'resource_path': entry.get('resource_path'),
        'access_kind': entry.get('access_kind'),
        'outcome': entry.get('outcome'),
        'duration_ms': dur_val,
    }
    if entry.get('operation_title'):
        body['operation_title'] = entry.get('operation_title')
    if stat is not None:
        try:
            hs = strict_json_nonneg_int(stat, field='http_status')
        except AgentClaimsValidationError:
            return
        body['http_status'] = hs
        body['status'] = hs
    if entry.get('error_code'):
        body['error_code'] = str(entry['error_code'])
    if entry.get('approval_request_id'):
        body['approval_request_id'] = str(entry['approval_request_id'])
    if entry.get('arguments_hash'):
        body['arguments_hash'] = str(entry['arguments_hash'])
    if entry.get('request_id'):
        body['request_id'] = str(entry['request_id'])
    gv = entry.get('grant_version')
    if gv is not None:
        try:
            body['grant_version'] = strict_json_nonneg_int(gv, field='grant_version')
        except AgentClaimsValidationError:
            return
    if entry.get('global_company_id') is not None:
        body['global_company_id'] = str(entry['global_company_id'])
    if entry.get('company_binding_version') is not None:
        try:
            body['company_binding_version'] = strict_json_nonneg_int(
                entry['company_binding_version'], field='company_binding_version'
            )
        except AgentClaimsValidationError:
            return
    try:
        from apps.email.models import CompanyProfile

        profile = CompanyProfile.objects.filter(company_id=cc_val).only('name').first()
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
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP call log forward failed (%s).', type(exc).__name__)
