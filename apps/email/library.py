"""The template library: built-in designs from renderer/library/ plus company templates."""

from __future__ import annotations

import json
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.email.document import empty_document, validate_document
from apps.email.models import LibraryTemplate
from apps.email.rendering import compose, compose_many, document_tokens
from apps.email.service import SendError
from apps.email.substitution import SubstitutionError
from apps.email.theming import clean_theme, theme_colors, tokenize

logger = logging.getLogger(__name__)

SETS = (
    ('barebone', 'Barebone'),
    ('matte', 'Matte'),
    ('protocol', 'Protocol'),
    ('arcane', 'Arcane'),
    ('studio', 'Studio'),
)
SET_KEYS = {key for key, _name in SETS}
# The tokens a library design may use before it is copied onto an event.
LIBRARY_VARIABLES = [
    {'token': 'company_name', 'type': 'string', 'required': False, 'example': 'Acme', 'is_url': False},
    {'token': 'action_url', 'type': 'url', 'required': False, 'example': 'https://example.com', 'is_url': True},
]


def library_dir() -> Path:
    return Path(settings.BASE_DIR) / 'renderer' / 'library'


@lru_cache(maxsize=None)
def set_head(set_key: str) -> str:
    """Fonts and mobile rules shared by every design of a set. Empty for blank templates."""
    if set_key not in SET_KEYS:
        return ''
    path = library_dir() / set_key / 'head.css'
    return path.read_text(encoding='utf-8') if path.exists() else ''


def seed_files() -> list[dict]:
    """The built-in designs, their colors tokenized as theme roles."""
    seeds = []
    for set_key, _name in SETS:
        folder = library_dir() / set_key
        for path in sorted(folder.glob('*.json')):
            seed = json.loads(path.read_text(encoding='utf-8'))
            seed['document'] = tokenize(seed['document'], seed['set'])
            seeds.append(seed)
    return seeds


def compose_library(row: LibraryTemplate) -> None:
    row.html, row.text = compose(
        row.document, head=set_head(row.set), preheader=row.preheader, colors=theme_colors(row.theme)
    )


def sync_builtins() -> int:
    """Create or refresh the built-ins. Returns how many were (re)composed."""
    seeds = seed_files()
    existing = {row.key: row for row in LibraryTemplate.objects.filter(built_in=True)}
    stale = []
    for seed in seeds:
        row = existing.pop(seed['key'], None) or LibraryTemplate(key=seed['key'], built_in=True)
        changed = (
            row.pk is None
            or not row.html
            or row.document != seed['document']
            or row.preheader != seed['preheader']
            or row.set != seed['set']
        )
        row.set = seed['set']
        row.name = seed['name']
        row.subject = seed['subject']
        row.preheader = seed['preheader']
        row.document = seed['document']
        row.company_id = None
        if changed:
            stale.append(row)
        else:
            row.save()
    if stale:
        try:
            rendered = compose_many(
                [{'document': row.document, 'head': set_head(row.set), 'preheader': row.preheader} for row in stale]
            )
        except SendError:
            logger.warning('library sync could not compose %s built-ins; they compose on first use', len(stale))
            rendered = [('', '')] * len(stale)
        for row, (html, text) in zip(stale, rendered):
            row.html, row.text = html, text
            row.save()
    LibraryTemplate.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    return len(stale)


def ensure_composed(row: LibraryTemplate) -> LibraryTemplate:
    if not row.html:
        compose_library(row)
        row.save(update_fields=['html', 'text', 'updated_at'])
    return row


def visible_library(company_id: int):
    from django.db.models import Q

    return LibraryTemplate.objects.filter(Q(built_in=True) | Q(company_id=company_id))


def library_template(company_id: int, raw_id) -> LibraryTemplate:
    try:
        template_id = int(raw_id)
    except (TypeError, ValueError):
        raise SendError(404, 'library_not_found')
    row = visible_library(company_id).filter(pk=template_id).first()
    if row is None:
        raise SendError(404, 'library_not_found')
    return row


def library_summary(row: LibraryTemplate, *, include_html: bool = True) -> dict:
    payload = {
        'id': row.pk,
        'key': row.key,
        'set': row.set,
        'name': row.name,
        'built_in': row.built_in,
        'company_id': row.company_id,
        'subject': row.subject,
        'preheader': row.preheader,
        'updated_at': row.updated_at.isoformat().replace('+00:00', 'Z') if row.updated_at else None,
    }
    if include_html:
        payload['html'] = row.html
    return payload


def library_detail(row: LibraryTemplate) -> dict:
    payload = library_summary(row)
    payload.update(
        {
            'document': row.document,
            'theme': row.theme or {},
            'text': row.text,
            'head': set_head(row.set),
            'variables': LIBRARY_VARIABLES,
        }
    )
    return payload


def _content(data: dict, *, subject: str, preheader: str, document: dict) -> tuple[str, str, dict]:
    if 'subject' in data:
        subject = str(data.get('subject') or '')[:255]
    if 'preheader' in data:
        preheader = str(data.get('preheader') or '')[:255]
    if 'document' in data:
        document = validate_document(data.get('document'))
    try:
        document_tokens(document, subject, preheader)
    except SubstitutionError as exc:
        raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
    return subject, preheader, document


def create_library_template(company_id: int, data: dict) -> LibraryTemplate:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    name = str(data.get('name') or '').strip()[:120]
    source = None
    if data.get('source_id') not in (None, ''):
        source = library_template(company_id, data.get('source_id'))
    if not name:
        name = f'{source.name} copy'[:120] if source else ''
    if not name:
        raise SendError(400, 'validation_failed', {'name': ['required']})
    subject, preheader, document = _content(
        data,
        subject=source.subject if source else '',
        preheader=source.preheader if source else '',
        document=source.document if source else empty_document(),
    )
    row = LibraryTemplate(
        key='company.' + secrets.token_hex(6),
        set=source.set if source else '',
        name=name,
        company_id=company_id,
        built_in=False,
        subject=subject,
        preheader=preheader,
        document=document,
        theme=clean_theme(data.get('theme')) if 'theme' in data else (source.theme if source else {}),
    )
    compose_library(row)
    with transaction.atomic():
        row.save()
    return row


def update_library_template(row: LibraryTemplate, data: dict) -> LibraryTemplate:
    if row.built_in:
        raise SendError(409, 'library_built_in')
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    if 'name' in data:
        name = str(data.get('name') or '').strip()[:120]
        if not name:
            raise SendError(400, 'validation_failed', {'name': ['required']})
        row.name = name
    row.subject, row.preheader, row.document = _content(
        data, subject=row.subject, preheader=row.preheader, document=row.document
    )
    if 'theme' in data:
        row.theme = clean_theme(data.get('theme'))
    compose_library(row)
    row.save()
    return row


def delete_library_template(row: LibraryTemplate) -> None:
    if row.built_in:
        raise SendError(409, 'library_built_in')
    row.delete()
