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


class McpDelegatedApprovalRoutingError(McpAgentCompanyMismatch):
    """Delegated agents must use the regional AI gateway for Identity approvals (HTTP ``409`` ``out_of_sync``)."""


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


# Identity ``_delegated_authority_fixed_payload`` (``d7fe351`` ``internal_views.py``): every HTTP **200** body uses these 15 keys only.
_DELEGATED_AUTHORITY_FIXED_200_KEYS = frozenset(
    {
        'authorized',
        'active',
        'revoked',
        'paused',
        'agent_id',
        'company_id',
        'global_agent_id',
        'global_company_id',
        'grant_version',
        'company_binding_version',
        'access_mode',
        'read_level',
        'allow_destructive',
        'services_allowed',
        'revoked_at',
    }
)


def _parse_live_delegated_authority_body(
    body: Any,
    *,
    agent_id: str,
    company_id: int,
    global_company_id: str,
    company_binding_version: int,
    grant_version: int,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """
    JSON body from Identity ``POST …/mcp-agent-delegated-authority`` (HTTP ``200``).

    Identity ``McpInternalDelegatedAgentAuthorityView`` (PR #135, head ``d7fe351``) returns the same **15** JSON keys on every HTTP **200**
    (``_delegated_authority_fixed_payload``): both live match and binding mismatch. ``authorized`` false means the caller must treat the row as denied (mismatch, or inactive or revoked agent).
    """
    if not isinstance(body, dict):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if 'agent' in body:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    keys = frozenset(body.keys())
    if keys != _DELEGATED_AUTHORITY_FIXED_200_KEYS:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')

    aid = str(agent_id or '').strip()
    try:
        uuid.UUID(aid)
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc

    def _strict_bool(raw: Any) -> bool:
        if raw is True or raw is False:
            return raw
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')

    rva = body.get('revoked_at')
    if rva is not None:
        if type(rva) is not int or isinstance(rva, bool) or rva < 0:
            raise McpAgentCompanyMismatch('authority_snapshot_invalid')

    svc = body.get('services_allowed')
    if not isinstance(svc, list) or not all(type(x) is str for x in svc):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')

    am = body.get('access_mode')
    rl = body.get('read_level')
    if type(am) is not str or not str(am).strip() or type(rl) is not str or not str(rl).strip():
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    _strict_bool(body.get('allow_destructive'))

    authz = body.get('authorized')
    if authz is False:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if authz is not True:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')

    if _strict_bool(body.get('active')) is not True:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if _strict_bool(body.get('revoked')):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if _strict_bool(body.get('paused')):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')

    row_agent = str(body.get('agent_id') or '').strip()
    row_global_agent = str(body.get('global_agent_id') or '').strip()
    for sid in (row_agent, row_global_agent):
        try:
            u = uuid.UUID(sid)
        except (ValueError, TypeError) as exc:
            raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
        if u.int == 0:
            raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if row_agent != aid:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    # SaaS catalog agent must not be confused with the regional token ``agent_id``.
    if row_global_agent == row_agent:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')

    cid_raw = body.get('company_id')
    if type(cid_raw) is not int or isinstance(cid_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if cid_raw < 1:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if int(cid_raw) != int(company_id):
        raise McpAgentCompanyMismatch('regional_company_mismatch')

    g_raw = body.get('global_company_id')
    try:
        ug = uuid.UUID(str(g_raw))
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
    if ug.int == 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if str(ug) != str(global_company_id):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')

    cbv_raw = body.get('company_binding_version')
    gv_raw = body.get('grant_version')
    if type(cbv_raw) is not int or isinstance(cbv_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if cbv_raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if type(gv_raw) is not int or isinstance(gv_raw, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if gv_raw < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if int(cbv_raw) != int(company_binding_version):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if int(gv_raw) != int(grant_version):
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')

    return {
        'agent_id': aid,
        'company_id': int(cid_raw),
        'global_company_id': str(ug),
        'company_binding_version': int(cbv_raw),
        'grant_version': int(gv_raw),
    }


# Tests and contract checks compare this to Identity ``_delegated_authority_fixed_payload`` keys (15 total).
_DELEGATED_AUTHORITY_SUCCESS_KEYS = _DELEGATED_AUTHORITY_FIXED_200_KEYS


def _delegated_authority_assertion_headers() -> dict[str, str]:
    _require_service_assertions()
    origin = identity_origin()
    assertion = build_mcp_delegated_authority_assertion(audience_url=origin)
    return {'Authorization': f'Bearer {assertion}', 'Content-Type': 'application/json'}


def _delegated_authority_request_json(
    agent_id: str,
    company_id: int,
    *,
    global_company_id: str,
    company_binding_version: int,
    grant_version: int,
) -> dict[str, Any]:
    """Strict POST JSON: verified token fields only (no echo of unknown data)."""
    aid = str(agent_id or '').strip()
    try:
        uuid.UUID(aid)
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
    try:
        g = uuid.UUID(str(global_company_id))
    except (ValueError, TypeError) as exc:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid') from exc
    if g.int == 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if type(company_id) is not int or isinstance(company_id, bool) or company_id < 1:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if type(company_binding_version) is not int or isinstance(company_binding_version, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if company_binding_version < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if type(grant_version) is not int or isinstance(grant_version, bool):
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    if grant_version < 0:
        raise McpAgentCompanyMismatch('authority_snapshot_invalid')
    return {
        'agent_id': aid,
        'company_id': company_id,
        'global_company_id': str(g),
        'company_binding_version': company_binding_version,
        'grant_version': grant_version,
    }


def _fetch_live_delegated_authority(
    agent_id: str,
    company_id: int,
    *,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """
    Current delegated-agent row from Identity (not only the signed revocation snapshot).

    Uses a dedicated service JWT (``aud`` ``shellui-mcp-agent-delegated-authority``,
    scope ``mcp.agent.delegated.authority.read``, ``target`` = Identity canonical issuer URL).
    Fails closed on transport, malformed JSON, or HTTP ``401`` (service assertion misconfiguration maps to ``503``).
    """
    _require_service_assertions()
    base = identity_api_base()
    if not base:
        raise McpIdentityUnavailable('identity_service_url_not_configured')
    url = f'{base}/api/v1/mcp/internal/mcp-agent-delegated-authority'
    body = _delegated_authority_request_json(
        str(agent_id),
        int(company_id),
        global_company_id=str(claims['global_company_id']),
        company_binding_version=int(claims['company_binding_version']),
        grant_version=int(claims['grant_version']),
    )
    try:
        response = requests.post(
            url,
            json=body,
            headers=_delegated_authority_assertion_headers(),
            timeout=getattr(settings, 'JWKS_TIMEOUT', 15),
        )
    except (requests.RequestException, OSError) as exc:
        logger.warning('MCP delegated authority fetch failed (%s).', type(exc).__name__)
        raise McpIdentityUnavailable('delegated_authority_fetch_failed') from exc
    if response.status_code == 404:
        raise McpAgentCompanyMismatch('authority_binding_missing')
    if response.status_code == 401:
        raise McpIdentityUnavailable('delegated_authority_assertion_rejected')
    if response.status_code == 403:
        raise McpAgentCompanyMismatch('authority_snapshot_mismatch')
    if response.status_code >= 500:
        raise McpIdentityUnavailable('delegated_authority_fetch_failed')
    if response.status_code != 200:
        raise McpIdentityUnavailable('delegated_authority_fetch_failed')
    try:
        payload = response.json()
    except ValueError as exc:
        raise McpIdentityUnavailable('delegated_authority_response_invalid') from exc
    return _parse_live_delegated_authority_body(
        payload,
        agent_id=str(agent_id),
        company_id=int(company_id),
        global_company_id=str(claims['global_company_id']),
        company_binding_version=int(claims['company_binding_version']),
        grant_version=int(claims['grant_version']),
        claims=claims,
    )


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
    _fetch_live_delegated_authority(str(agent_id), int(company_id), claims=claims)


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
    matching = [r for r in rows if str(r.get('agent_id') or '') == agent_id]
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
            verify_live_delegated_authority_against_token(agent_id, int(company_id), claims)
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
        'mcp_access',
        'mcp_read',
        'mcp_destructive',
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
        payload,
    )


def _approval_post_out_of_sync(response: Any) -> bool:
    if getattr(response, 'status_code', None) != 409:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    return str(data.get('error_code') or '').strip() == 'out_of_sync'


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
        if _approval_post_out_of_sync(response):
            raise McpDelegatedApprovalRoutingError('approval_routed_via_ai_only')
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
    except McpDelegatedApprovalRoutingError:
        raise
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
        if _approval_post_out_of_sync(response):
            raise McpDelegatedApprovalRoutingError('approval_routed_via_ai_only')
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            raise McpIdentityUnavailable('approval_consume_response_invalid')
        if body.get('consumed') is not True:
            raise McpIdentityUnavailable('approval_consume_not_true')
        return True
    except McpDelegatedApprovalRoutingError:
        raise
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
        if _approval_post_out_of_sync(response):
            raise McpDelegatedApprovalRoutingError('approval_routed_via_ai_only')
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
    except McpDelegatedApprovalRoutingError:
        raise
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
    gci_live = entry.get('global_company_id')
    if (
        gci_live is not None
        and cbv_for_live is not None
        and gv_for_live is not None
        and entry.get('agent_id') is not None
    ):
        try:
            gv_i = strict_json_nonneg_int(gv_for_live, field='grant_version')
            cbv_i = strict_json_nonneg_int(cbv_for_live, field='company_binding_version')
        except AgentClaimsValidationError:
            return
        live_claims: dict[str, Any] = {
            'global_company_id': gci_live,
            'company_binding_version': cbv_i,
            'grant_version': gv_i,
        }
        try:
            verify_live_delegated_authority_against_token(str(entry['agent_id']), cc_val, live_claims)
        except McpIdentityUnavailable:
            return
        except McpAgentCompanyMismatch as exc:
            code = str(exc.args[0]) if exc.args else ''
            entry = dict(entry)
            entry['outcome'] = 'denied'
            if code == 'authority_binding_missing':
                entry['error_code'] = 'authority_binding_missing'
            elif code in {'authority_snapshot_mismatch', 'regional_company_mismatch'}:
                entry['error_code'] = 'authority_mismatch'
            elif code == 'authority_snapshot_invalid':
                entry['error_code'] = 'request_failed'
            else:
                entry['error_code'] = 'authority_mismatch'
    assertion = build_mcp_call_log_assertion(audience_url=origin, **signing_kwargs())
    url = f'{base}/api/v1/mcp/internal/mcp-call-logs'
    stat = entry.get('http_status')
    dur_raw = entry.get('duration_ms')
    try:
        dur_val = strict_json_nonneg_int(dur_raw, field='duration_ms')
    except AgentClaimsValidationError:
        return
    ak = str(entry.get('access_kind') or '').strip().lower()
    if ak not in ('read', 'write', 'destructive'):
        ak = 'unknown'
    try:
        hs = strict_json_nonneg_int(stat, field='http_status') if stat is not None else 0
    except AgentClaimsValidationError:
        return
    body: dict[str, Any] = {
        'agent_id': str(entry.get('agent_id') or ''),
        'company_id': cc_val,
        'service': str(entry.get('service') or '')[:32],
        'tool': str(entry.get('tool') or '')[:255],
        'method': 'POST',
        'path': '/mcp',
        'status': hs,
        'duration_ms': dur_val,
        'result': '',
        'outcome': str(entry.get('outcome') or '').strip().lower(),
        'access_kind': ak,
    }
    try:
        gv_entry = entry.get('grant_version')
        if gv_entry is None:
            body['grant_version'] = 0
        else:
            body['grant_version'] = strict_json_nonneg_int(gv_entry, field='grant_version')
    except AgentClaimsValidationError:
        return
    gci_entry = entry.get('global_company_id')
    cbv_entry = entry.get('company_binding_version')
    if gci_entry is not None and cbv_entry is not None:
        try:
            body['global_company_id'] = str(gci_entry)
            body['company_binding_version'] = strict_json_nonneg_int(cbv_entry, field='company_binding_version')
        except AgentClaimsValidationError:
            return
    if entry.get('error_code'):
        body['error_code'] = str(entry['error_code'])
    appr = entry.get('approval_request_id')
    if appr:
        try:
            body['approval_id'] = str(uuid.UUID(str(appr)))
        except (ValueError, TypeError):
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
