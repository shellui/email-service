"""Placeholder substitution. No logic, no Django or Jinja tags."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlparse

TOKEN_RE = re.compile(
    r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:\|\s*default\s*:\s*"([^"]*)"\s*)?\}\}'
)
TAG_RE = re.compile(r'\{%|%\}|\{#|#\}')

_ALLOWED_URL_SCHEMES = {'https', 'mailto', 'tel'}


class SubstitutionError(Exception):
    def __init__(self, code: str, field: str):
        self.code = code
        self.field = field
        super().__init__(code)


def find_tokens(source: str) -> set[str]:
    return {match.group(1) for match in TOKEN_RE.finditer(source or '')}


def reject_template_tags(source: str) -> None:
    if TAG_RE.search(source or ''):
        raise SubstitutionError('template_tags_forbidden', 'document')


def escape_keeping_tokens(text: str) -> str:
    """HTML-escape literal text and leave ``{{ token }}`` placeholders intact."""
    parts: list[str] = []
    last = 0
    for match in TOKEN_RE.finditer(text or ''):
        parts.append(escape(text[last : match.start()], quote=True))
        parts.append(match.group(0))
        last = match.end()
    parts.append(escape((text or '')[last:], quote=True))
    return ''.join(parts)


def _lookup(variables: dict, token: str, default: str | None) -> tuple[str, bool]:
    if token in variables and variables[token] not in (None, ''):
        return str(variables[token]), True
    if default is not None:
        return default, True
    return '', False


def _validate_url(value: str, *, allow_http_localhost: bool, allowed_hosts: set[str] | None) -> str:
    parsed = urlparse(value.strip())
    scheme = (parsed.scheme or '').lower()
    host = (parsed.hostname or '').lower()
    if scheme == 'http' and allow_http_localhost and host in {'localhost', '127.0.0.1'}:
        return value.strip()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise SubstitutionError('invalid_url_scheme', 'variables')
    if scheme == 'https' and allowed_hosts is not None and host not in allowed_hosts:
        raise SubstitutionError('variable_url_not_allowed', 'variables')
    return value.strip()


def substitute(
    source: str,
    variables: dict,
    *,
    html: bool,
    subject: bool = False,
    url_tokens: dict[str, set[str] | None] | None = None,
    allow_http_localhost: bool = False,
) -> tuple[str, list[str]]:
    """
    Replace ``{{ token }}`` and ``{{ token|default:"x" }}``.

    Returns the rendered string and tokens that had neither a value nor a default.
    """
    missing: list[str] = []
    url_tokens = url_tokens or {}

    def replace(match: re.Match) -> str:
        token = match.group(1)
        default = match.group(2)
        value, present = _lookup(variables, token, default)
        if not present:
            missing.append(token)
            return ''
        if subject:
            value = value.replace('\r', '').replace('\n', '')
        if token in url_tokens or token.startswith('system.') and token.endswith('_url'):
            hosts = url_tokens.get(token)
            value = _validate_url(
                value,
                allow_http_localhost=allow_http_localhost,
                allowed_hosts=hosts,
            )
        if html:
            return escape(value, quote=True)
        return value

    rendered = TOKEN_RE.sub(replace, source or '')
    if subject:
        rendered = rendered.replace('\r', '').replace('\n', '')
    return rendered, missing
