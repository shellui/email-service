"""Identity ``McpInternalCallLogView`` error_code allowlist (``mcp_audit_codes.sanitize_error_code``, d7fe351).

email-service uses this for mapping local MCP audit codes before ``POST …/mcp-call-logs``.
Source of truth: identity-service ``apps/agents/mcp_audit_codes.py``.
"""

from __future__ import annotations

# Copied from Identity ``ALLOWED_ERROR_CODES`` at d7fe351 (keep in sync on Identity bumps).
IDENTITY_CALL_LOG_ALLOWED_ERROR_CODES = frozenset(
    {
        'bad_request',
        'unauthorized',
        'forbidden',
        'not_found',
        'conflict',
        'rate_limited',
        'timeout',
        'upstream_error',
        'internal_error',
        'unknown',
        'denied',
        'pending_approval',
        'waiting_approval',
        'expired',
        'out_of_sync',
        'identity_unreachable',
        'invalid_grant',
        'permission_changed',
        'request_expired',
        'downstream_error',
        'server_error',
    }
)


def sanitize_call_log_error_code_for_identity(raw: object) -> str | None:
    code = str(raw or '').strip().lower()[:64]
    return code if code in IDENTITY_CALL_LOG_ALLOWED_ERROR_CODES else None
