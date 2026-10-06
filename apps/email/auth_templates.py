"""Auth-lane copies must keep the catalog link, and every link must be a declared URL or an allowlisted host.

A link variable may only sit in a link target or in visible text. Anywhere else (an
image ``src``, a ``style``, an ``alt``) a mail client or an image proxy would send the
sign-in link to a host the company chose, so publish refuses it.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.conf import settings

from apps.email.document import links, texts
from apps.email.rendering import document_tokens
from apps.email.service import SendError
from apps.email.substitution import TOKEN_RE, SubstitutionError, find_tokens, reject_template_tags

# A literal URL in the prose, beside the real button, is a second link the recipient can click.
_LITERAL_LINK = re.compile(
    r'(?i)(?:\b(?:https?://|mailto:|tel:)[^\s<>"\']+|www\.[^\s<>"\']+|<\s*a\b|\bhref\s*=)'
)
# Auth mail has no unsubscribe or preferences page, so those system links stay empty.
_AUTH_SYSTEM_LINKS = {'system.message_id'}
# Looser than ``TOKEN_RE`` on purpose: any ``{{ name`` counts, whatever follows it.
_TOKEN_START = re.compile(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)')
_LINK_NODES = {'button', 'image'}


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


def _link_error(definition: dict, href: str) -> str | None:
    try:
        reject_template_tags(href)
    except SubstitutionError:
        return 'template_tags_forbidden'
    href_tokens = find_tokens(href)
    allowed_tokens = _declared_url_tokens(definition) | _AUTH_SYSTEM_LINKS
    if any(token not in allowed_tokens for token in href_tokens):
        return 'token_not_allowed'
    leftover = TOKEN_RE.sub('', href).strip()
    if not href_tokens and not leftover:
        return 'required'
    if leftover and not _literal_host_allowed(leftover):
        return 'host_not_allowed'
    return None


def auth_link_allowed(definition: dict, href: str) -> bool:
    return _link_error(definition, href) is None


def validate_auth_template(definition: dict, document: dict, subject: str, preheader: str) -> None:
    """Reject an auth copy that drops the sign-in link or links anywhere else."""
    if not definition or definition.get('lane_class') != 'auth':
        return
    try:
        tokens = document_tokens(document, subject, preheader)
    except SubstitutionError as exc:
        raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
    missing = sorted(_required_url_tokens(definition) - tokens)
    if missing:
        raise SendError(400, 'auth_link_missing', {token: ['required'] for token in missing})
    for href in links(document):
        error = _link_error(definition, href)
        if error == 'template_tags_forbidden':
            raise SendError(400, 'validation_failed', {'document': [error]})
        if error:
            raise SendError(400, 'auth_link_host_not_allowed', {'href': [error]})
    _reject_misplaced_link_tokens(definition, document)
    _reject_literal_links(definition, document, subject, preheader)


def _misplaced_strings(value, *, allowed: bool = False):
    """Every string in ``value`` that is not a link target or the text of a text node."""
    if isinstance(value, str):
        if not allowed:
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _misplaced_strings(item)
        return
    if not isinstance(value, dict):
        return
    node_type = value.get('type')
    for key, item in value.items():
        if key == 'text' and node_type == 'text' and isinstance(item, str):
            continue
        if key == 'attrs' and isinstance(item, dict):
            href_allowed = node_type in _LINK_NODES or node_type == 'link'
            for attr, attr_value in item.items():
                yield from _misplaced_strings(attr_value, allowed=href_allowed and attr == 'href')
            continue
        yield from _misplaced_strings(item)


def _reject_misplaced_link_tokens(definition: dict, document: dict) -> None:
    link_tokens = _declared_url_tokens(definition)
    for value in _misplaced_strings(document):
        if any(match.group(1) in link_tokens for match in _TOKEN_START.finditer(value)):
            raise SendError(400, 'auth_link_misplaced', {'document': ['link_token_misplaced']})


def _prose_without_allowed_tokens(text: str, allowed_tokens: set[str]) -> str:
    def replace(match: re.Match) -> str:
        return '' if match.group(1) in allowed_tokens else match.group(0)

    return TOKEN_RE.sub(replace, text or '')


def _reject_literal_links(definition: dict, document: dict, subject: str, preheader: str) -> None:
    allowed = _required_url_tokens(definition)
    for name, value in (('subject', subject), ('preheader', preheader)):
        if _LITERAL_LINK.search(_prose_without_allowed_tokens(value, allowed)):
            raise SendError(400, 'auth_literal_link', {name: ['literal_url']})
    for text in texts(document):
        if _LITERAL_LINK.search(_prose_without_allowed_tokens(text, allowed)):
            raise SendError(400, 'auth_literal_link', {'document': ['literal_url']})
