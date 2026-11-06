"""Shellui MCP agent JWT validation and principals."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

from apps.authapi.authentication import _safe_jwt_meta
from apps.authapi.jwks_client import get_jwks_client
from apps.authapi.principal import EmailPrincipal
from shellui_django.mcp.resources import access_token_audience_matches, mcp_resource_from_origin

logger = logging.getLogger(__name__)

MCP_SERVICE_ID = 'email'

_VALID_MCP_ACCESS = frozenset({'read_only', 'ask', 'full'})
_VALID_MCP_READ = frozenset({'safe', 'sensitive'})


def _strict_bool_claim(raw: Any, *, field: str) -> bool:
    if raw is True or raw is False:
        return raw
    if raw in {0, 1}:
        return bool(raw)
    raise exceptions.AuthenticationFailed(f'Agent token has invalid {field}.')


def _normalize_company_claim(raw: Any) -> int:
    if isinstance(raw, str):
        s = raw.strip()
        if not s.isdecimal():
            raise exceptions.AuthenticationFailed('Agent token has invalid company_id.')
        return int(s)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise exceptions.AuthenticationFailed('Agent token has invalid company_id.') from exc


def is_agent_principal(user) -> bool:
    if user is None:
        return False
    username = str(getattr(user, 'username', '') or '')
    if username.startswith('agent:'):
        return True
    claims = getattr(user, 'claims', None)
    return isinstance(claims, dict) and bool(claims.get('agent_id'))


def agent_token_company_id(user) -> int:
    """Company scope bound to an MCP agent principal (never from query/body)."""
    if not is_agent_principal(user):
        raise ValueError('Not an agent principal.')
    raw = getattr(user, 'company_id', None)
    try:
        return _normalize_company_claim(raw)
    except exceptions.AuthenticationFailed as exc:
        raise ValueError(str(exc)) from exc


_BODYLESS_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


def _requested_company_id(request) -> int | None:
    raw = None
    if hasattr(request, 'query_params'):
        raw = request.query_params.get('company_id')
    method = getattr(request, 'method', 'GET').upper()
    if raw in (None, '') and method not in _BODYLESS_METHODS:
        data = request.data
        if isinstance(data, dict) and 'company_id' in data:
            raw = data.get('company_id')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        from apps.email.service import SendError

        raise SendError(400, 'validation_failed', {'company_id': ['invalid']})


def resolve_company_id_for_request(request, *, allow_null: bool = False) -> int | None:
    """
    Resolve admin ``company_id`` for humans and service users.

    MCP agents always use JWT ``company_id`` and cannot override it via query or body.
    """
    from apps.email.service import SendError

    user = getattr(request, 'user', None)
    if is_agent_principal(user):
        token_company = agent_token_company_id(user)
        requested = _requested_company_id(request)
        if requested is not None and int(requested) != int(token_company):
            raise SendError(403, 'company_mismatch')
        return token_company
    raw = None
    if hasattr(request, 'query_params'):
        raw = request.query_params.get('company_id')
    method = getattr(request, 'method', 'GET').upper()
    if raw in (None, '') and method not in _BODYLESS_METHODS:
        data = request.data
        if isinstance(data, dict) and 'company_id' in data:
            raw = data.get('company_id')
    if raw in (None, ''):
        if getattr(user, 'company_id', None) is not None and not getattr(user, 'is_service', False):
            return int(user.company_id)
        if allow_null:
            return None
        raise SendError(400, 'validation_failed', {'company_id': ['required']})
    if raw is None and allow_null:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SendError(400, 'validation_failed', {'company_id': ['invalid']})


def mcp_resource_audience(*, request=None) -> str:
    """RFC 8707 resource indicator for this service (``{origin}/mcp``)."""
    base = (getattr(settings, 'EMAIL_PUBLIC_URL', None) or '').strip().rstrip('/')
    if not base and request is not None:
        base = request.build_absolute_uri('/').rstrip('/')
    if not base:
        base = (getattr(settings, 'PUBLIC_BASE_URL', None) or '').strip().rstrip('/')
    resource = mcp_resource_from_origin(base)
    if not resource:
        raise exceptions.AuthenticationFailed('MCP resource URL is not configured.')
    return resource


def _staff_agent_refused(claims: dict[str, Any]) -> bool:
    if claims.get('mcp_staff') or claims.get('is_staff'):
        return True
    meta = claims.get('user_metadata')
    if isinstance(meta, dict):
        if meta.get('is_staff') or meta.get('is_superuser') or meta.get('is_global_staff'):
            return True
    return False


def _scope_allows_email(scope: Any) -> bool:
    if scope is None:
        return False
    if isinstance(scope, str):
        parts = scope.split()
    elif isinstance(scope, (list, tuple)):
        parts = [str(item) for item in scope]
    else:
        return False
    return MCP_SERVICE_ID in parts


def _decode_agent_jwt(raw_token: str, *, audience: str) -> dict[str, Any]:
    algorithms = list(getattr(settings, 'JWT_ALGORITHMS', ['RS256']))
    options = {
        'require': ['exp', 'agent_id'],
        'verify_aud': True,
        'verify_iss': bool(settings.IDENTITY_ISSUER),
    }
    decode_kwargs: dict = {
        'algorithms': algorithms,
        'options': options,
        'audience': audience,
    }
    if settings.IDENTITY_ISSUER:
        decode_kwargs['issuer'] = settings.IDENTITY_ISSUER

    errors: list[Exception] = []
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key(raw_token)
        if signing_key is not None:
            return jwt.decode(raw_token, key=signing_key.key, **decode_kwargs)
    except jwt.InvalidTokenError as exc:
        errors.append(exc)
    except Exception as exc:
        errors.append(exc)

    secret = getattr(settings, 'JWT_HS256_FALLBACK_SECRET', None)
    allow_hs256 = bool(secret) and (
        settings.DEBUG or getattr(settings, 'ALLOW_JWT_HS256_FALLBACK', False)
    )
    if allow_hs256:
        try:
            kwargs = dict(decode_kwargs)
            kwargs['algorithms'] = ['HS256']
            return jwt.decode(raw_token, key=secret, **kwargs)
        except jwt.InvalidTokenError as exc:
            errors.append(exc)

    token_meta = _safe_jwt_meta(raw_token)
    logger.warning(
        'Agent JWT rejected. %s audience=%s errors=%s',
        token_meta,
        audience,
        [type(exc).__name__ for exc in errors] or ['no_signing_key'],
    )
    raise exceptions.AuthenticationFailed('Agent token is invalid.')


def agent_claims_from_token(raw_token: str, *, request=None) -> dict[str, Any]:
    resource_aud = mcp_resource_audience(request=request)
    claims = _decode_agent_jwt(raw_token, audience=resource_aud)
    if not access_token_audience_matches(claims, resource_aud):
        raise exceptions.AuthenticationFailed('Agent audience must match this MCP resource.')
    agent_id = claims.get('agent_id')
    if not agent_id:
        raise exceptions.AuthenticationFailed('Agent token required.')
    sub = str(claims.get('sub') or '')
    if not sub.startswith('agent:'):
        raise exceptions.AuthenticationFailed('Invalid agent subject.')
    if not _scope_allows_email(claims.get('scope')):
        raise exceptions.AuthenticationFailed('Agent is not allowed to use email.')
    if _staff_agent_refused(claims):
        raise exceptions.AuthenticationFailed('Staff accounts cannot use MCP agents.')
    raw_access = claims.get('mcp_access')
    if raw_access is None:
        claims['mcp_access'] = 'read_only'
    else:
        access = str(raw_access).strip()
        if access not in _VALID_MCP_ACCESS:
            raise exceptions.AuthenticationFailed('Agent token has invalid mcp_access.')
        claims['mcp_access'] = access
    raw_read = claims.get('mcp_read')
    if raw_read is None:
        claims['mcp_read'] = 'safe'
    else:
        read_level = str(raw_read).strip()
        if read_level not in _VALID_MCP_READ:
            raise exceptions.AuthenticationFailed('Agent token has invalid mcp_read.')
        claims['mcp_read'] = read_level
    dest_raw = claims.get('mcp_destructive')
    claims['mcp_destructive'] = _strict_bool_claim(False if dest_raw is None else dest_raw, field='mcp_destructive')
    claims['company_id'] = _normalize_company_claim(claims.get('company_id'))
    # Regional numeric company_id plus optional global delegation claims (identity #135).
    if 'grant_version' in claims:
        gv_raw = claims.get('grant_version')
        if gv_raw is None:
            claims.pop('grant_version', None)
        else:
            if isinstance(gv_raw, bool):
                raise exceptions.AuthenticationFailed('Agent token has invalid grant_version.')
            try:
                gv = int(gv_raw)
            except (TypeError, ValueError) as exc:
                raise exceptions.AuthenticationFailed('Agent token has invalid grant_version.') from exc
            if gv < 0:
                raise exceptions.AuthenticationFailed('Agent token has invalid grant_version.')
            claims['grant_version'] = gv
    if 'global_company_id' in claims:
        g_raw = claims.get('global_company_id')
        if g_raw is None:
            claims.pop('global_company_id', None)
        else:
            try:
                claims['global_company_id'] = str(uuid.UUID(str(g_raw)))
            except (ValueError, TypeError) as exc:
                raise exceptions.AuthenticationFailed('Agent token has invalid global_company_id.') from exc
    if 'company_binding_version' in claims:
        cbv_raw = claims.get('company_binding_version')
        if cbv_raw is None:
            claims.pop('company_binding_version', None)
        else:
            if isinstance(cbv_raw, bool):
                raise exceptions.AuthenticationFailed('Agent token has invalid company_binding_version.')
            try:
                cbv = int(cbv_raw)
            except (TypeError, ValueError) as exc:
                raise exceptions.AuthenticationFailed('Agent token has invalid company_binding_version.') from exc
            if cbv < 0:
                raise exceptions.AuthenticationFailed('Agent token has invalid company_binding_version.')
            claims['company_binding_version'] = cbv
    has_global = claims.get('global_company_id') is not None
    has_binding = claims.get('company_binding_version') is not None
    if has_global != has_binding:
        raise exceptions.AuthenticationFailed('agent_token_authority_incomplete')
    if has_global and 'grant_version' not in claims:
        raise exceptions.AuthenticationFailed('agent_token_authority_incomplete')
    return dict(claims)


def agent_principal_from_claims(claims: dict[str, Any]) -> EmailPrincipal:
    if claims.get('company_id') is None:
        raise exceptions.AuthenticationFailed('Agent token missing company_id.')
    try:
        company_id = _normalize_company_claim(claims.get('company_id'))
    except exceptions.AuthenticationFailed as exc:
        raise exc
    return EmailPrincipal(
        user_id=0,
        company_id=company_id,
        email='',
        username=f'agent:{claims.get("agent_id")}',
        is_company_owner=True,
        claims=claims,
    )


def authenticate_agent_request(request) -> tuple[EmailPrincipal, dict[str, Any]] | None:
    header = authentication.get_authorization_header(request).decode('utf-8')
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0] != 'Bearer':
        return None
    raw_token = parts[1]
    if raw_token.startswith('esk_'):
        return None
    try:
        claims = agent_claims_from_token(raw_token, request=request)
    except exceptions.AuthenticationFailed:
        return None
    principal = agent_principal_from_claims(claims)
    return principal, claims
