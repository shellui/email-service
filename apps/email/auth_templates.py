"""Auth-lane template overrides must keep the catalog link and an allowlisted button."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.conf import settings

from apps.email.rendering import document_tokens
from apps.email.service import SendError
from apps.email.substitution import TOKEN_RE, SubstitutionError, find_tokens, reject_template_tags

# A literal URL in the prose, beside the real button, is a second link the recipient can click.
_LITERAL_LINK = re.compile(
    r'(?i)(?:\b(?:https?://|mailto:|tel:)[^\s<>"\']+|www\.[^\s<>"\']+|<\s*a\b|\bhref\s*=)'
)


def _required_url_tokens(definition: dict) -> set[str]:
    tokens = set()
    for variable in definition.get('variables') or []:
        if variable.get('type') == 'url' and variable.get('required'):
            tokens.add(variable['token'])
    return tokens


def _declared_url_tokens(definition: dict) -> set[str]:
    tokens = set()
    for variable in definition.get('variables') or []:
        if variable.get('type') == 'url' or variable.get('is_url'):
            tokens.add(variable['token'])
    return tokens


def _literal_host_allowed(href: str) -> bool:
    parsed = urlparse(href.strip())
    if (parsed.scheme or '').lower() != 'https':
        return False
    host = (parsed.hostname or '').lower()
    allowed = {item.lower() for item in settings.EMAIL_AUTH_LINK_HOSTS}
    return bool(host) and host in allowed


def validate_auth_template(definition: dict, document: dict, subject: str, preheader: str) -> None:
    """Reject an auth override that drops the sign-in link or points a button elsewhere."""
    if not definition or definition.get('lane_class') != 'auth':
        return
    try:
        tokens = document_tokens(document, subject, preheader)
    except SubstitutionError as exc:
        raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
    missing = sorted(_required_url_tokens(definition) - tokens)
    if missing:
        raise SendError(400, 'auth_link_missing', {token: ['required'] for token in missing})
    allowed_tokens = _declared_url_tokens(definition)
    for block in document.get('blocks') or []:
        if block.get('type') != 'button':
            continue
        href = str(block.get('href') or '')
        try:
            reject_template_tags(href)
        except SubstitutionError as exc:
            raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
        href_tokens = find_tokens(href)
        unknown = sorted(token for token in href_tokens if token not in allowed_tokens and not token.startswith('system.'))
        if unknown:
            raise SendError(400, 'auth_link_host_not_allowed', {'href': ['token_not_allowed']})
        leftover = TOKEN_RE.sub('', href).strip()
        if not href_tokens and not leftover:
            raise SendError(400, 'auth_link_host_not_allowed', {'href': ['required']})
        if leftover and not _literal_host_allowed(leftover):
            raise SendError(400, 'auth_link_host_not_allowed', {'href': ['host_not_allowed']})
    _reject_literal_links(definition, document, subject, preheader)


def _prose_without_allowed_tokens(text: str, allowed_tokens: set[str]) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token in allowed_tokens:
            return ''
        return match.group(0)

    return TOKEN_RE.sub(replace, text or '')


def _reject_literal_links(definition: dict, document: dict, subject: str, preheader: str) -> None:
    allowed = _required_url_tokens(definition)
    fields = (
        ('subject', subject),
        ('preheader', preheader),
        ('preview', str(document.get('preview') or '')),
    )
    for name, value in fields:
        if _LITERAL_LINK.search(_prose_without_allowed_tokens(value, allowed)):
            raise SendError(400, 'auth_literal_link', {name: ['literal_url']})
    for block in document.get('blocks') or []:
        kind = block.get('type')
        if kind == 'button':
            text = str(block.get('text') or '')
        elif kind in {'heading', 'text', 'footer'}:
            text = str(block.get('text') or '')
        else:
            continue
        if _LITERAL_LINK.search(_prose_without_allowed_tokens(text, allowed)):
            raise SendError(400, 'auth_literal_link', {'document': ['literal_url']})
