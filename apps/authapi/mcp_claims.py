"""Strict normalization for MCP agent token claims (shared by JWT auth and EmailMcpServer)."""

from __future__ import annotations

import uuid
from typing import Any

_VALID_MCP_ACCESS = frozenset({'read_only', 'ask', 'full'})
_VALID_MCP_READ = frozenset({'safe', 'sensitive'})
_VALID_MCP_AUTHORITY = frozenset({'personal', 'delegated'})


class AgentClaimsValidationError(ValueError):
    """Invalid agent claims surface for MCP after JWT crypto checks."""


def _strict_bool_claim(raw: Any, *, field: str) -> bool:
    if raw is True or raw is False:
        return raw
    if raw in {0, 1}:
        return bool(raw)
    raise AgentClaimsValidationError(field)


def strict_json_nonneg_int(raw: Any, *, field: str) -> int:
    """JSON integer claims only: reject bool, float, str, and any non-int type."""
    if type(raw) is not int or isinstance(raw, bool):
        raise AgentClaimsValidationError(field)
    if raw < 0:
        raise AgentClaimsValidationError(field)
    return raw


def normalize_mcp_agent_claims(agent_claims: dict[str, Any]) -> dict[str, Any]:
    """
    Validate claims Identity mints for Shellui MCP.

    - ``company_id``: JSON integer only (no strings, floats, or bools).
    - Optional ``mcp_authority``: when present, ``personal`` or ``delegated`` only (reject ``global_delegated`` and other values).
    - Delegation: when ``global_company_id``, ``company_binding_version``, and ``grant_version`` are present,
      ``mcp_authority`` must be ``delegated``. Personal agents omit the global triple; do not send ``delegated`` without the full triple.
    """
    access_raw = agent_claims.get('mcp_access')
    if access_raw is None:
        access = 'read_only'
    else:
        access = str(access_raw).strip()
        if access not in _VALID_MCP_ACCESS:
            raise AgentClaimsValidationError('mcp_access')
    read_raw = agent_claims.get('mcp_read')
    if read_raw is None:
        read_level = 'safe'
    else:
        read_level = str(read_raw).strip()
        if read_level not in _VALID_MCP_READ:
            raise AgentClaimsValidationError('mcp_read')
    dest_raw = agent_claims.get('mcp_destructive')
    destructive_flag = _strict_bool_claim(False if dest_raw is None else dest_raw, field='mcp_destructive')

    company_id = strict_json_nonneg_int(agent_claims.get('company_id'), field='company_id')

    authority_raw = agent_claims.get('mcp_authority')
    authority: str | None = None
    if authority_raw is not None:
        authority = str(authority_raw).strip()
        if authority not in _VALID_MCP_AUTHORITY:
            raise AgentClaimsValidationError('mcp_authority')

    gv: int | None = None
    if 'grant_version' in agent_claims:
        gv_raw = agent_claims.get('grant_version')
        if gv_raw is None:
            gv = None
        else:
            gv = strict_json_nonneg_int(gv_raw, field='grant_version')

    gci: str | None = None
    if 'global_company_id' in agent_claims:
        g_raw = agent_claims.get('global_company_id')
        if g_raw is None:
            gci = None
        else:
            try:
                u = uuid.UUID(str(g_raw))
            except (ValueError, TypeError) as exc:
                raise AgentClaimsValidationError('global_company_id') from exc
            if u.int == 0:
                raise AgentClaimsValidationError('global_company_id')
            gci = str(u)

    cbv: int | None = None
    if 'company_binding_version' in agent_claims:
        cbv_raw = agent_claims.get('company_binding_version')
        if cbv_raw is None:
            cbv = None
        else:
            cbv = strict_json_nonneg_int(cbv_raw, field='company_binding_version')

    has_gci = gci is not None
    has_cbv = cbv is not None
    has_gv = gv is not None
    if has_gci or has_cbv or has_gv:
        if not (has_gci and has_cbv and has_gv):
            raise AgentClaimsValidationError('delegation_incomplete')
        if authority != 'delegated':
            raise AgentClaimsValidationError('mcp_authority')
    else:
        if authority == 'delegated':
            raise AgentClaimsValidationError('delegation_incomplete')

    out = dict(agent_claims)
    out['mcp_access'] = access
    out['mcp_read'] = read_level
    out['mcp_destructive'] = destructive_flag
    out['company_id'] = company_id
    if gv is None:
        out.pop('grant_version', None)
    else:
        out['grant_version'] = gv
    if gci is None:
        out.pop('global_company_id', None)
    else:
        out['global_company_id'] = gci
    if cbv is None:
        out.pop('company_binding_version', None)
    else:
        out['company_binding_version'] = cbv
    if authority is not None:
        out['mcp_authority'] = authority
    else:
        out.pop('mcp_authority', None)
    return out
