"""Rules that decide which mail an event sends, and the company copies they send."""

from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.email.catalog import LANE_AUTH, all_definitions, get_definition
from apps.email.document import adapt_to_event, validate_document
from apps.email.library import library_template, set_head
from apps.email.models import EmailRule, EmailTemplate, LibraryTemplate, TemplateVersion
from apps.email.rendering import RENDERER_VERSION, checksum, compose, document_tokens
from apps.email.service import SendError
from apps.email.substitution import SubstitutionError

ALWAYS_AVAILABLE = {'recipient_email'}


def representative_version(template: EmailTemplate) -> TemplateVersion | None:
    if template.active_version:
        published = TemplateVersion.objects.filter(
            template=template,
            number=template.active_version,
            state=TemplateVersion.STATE_PUBLISHED,
        ).first()
        if published:
            return published
    return template.versions.order_by('-number').first()


def available_tokens(definition: dict) -> set[str]:
    tokens = {item['token'] for item in definition.get('variables') or []}
    return tokens | ALWAYS_AVAILABLE


def missing_variables(template: EmailTemplate, definition: dict) -> list[str]:
    version = representative_version(template)
    if version is None:
        return []
    used = {
        token
        for token in document_tokens(version.document, version.subject, version.preheader)
        if not token.startswith('system.')
    }
    return sorted(used - available_tokens(definition))


def fits_event(template: EmailTemplate, definition: dict) -> bool:
    return not missing_variables(template, definition)


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace('+00:00', 'Z')


def rule_payload(rule: EmailRule) -> dict:
    return {
        'id': rule.pk,
        'service': rule.service,
        'event_type': rule.event_type,
        'enabled': rule.enabled,
        'recipient_mode': rule.recipient_mode,
        'static_recipients': list(rule.static_recipients or []),
        'language': rule.language or '',
        'template_id': rule.template_id,
        'built_in': rule.built_in,
        'created_at': _iso(rule.created_at),
        'updated_at': _iso(rule.updated_at),
    }


def template_summary(template: EmailTemplate) -> dict:
    return {
        'id': template.pk,
        'template_key': template.template_key,
        'name': template.name,
        'event_type': template.event_type,
        'language': template.language,
        'company_id': template.company_id,
        'active_version': template.active_version,
        'source_key': template.source_key,
        'set': template.set,
        'head': set_head(template.set),
    }


def _definition_for(template: EmailTemplate) -> dict | None:
    return get_definition(template.event_type or '')


def _validated_content(definition: dict | None, document, subject: str, preheader: str) -> dict:
    document = validate_document(document)
    try:
        document_tokens(document, subject, preheader)
    except SubstitutionError as exc:
        raise SendError(400, 'validation_failed', {'document': [exc.code]}) from exc
    return document


def publish_version(template: EmailTemplate, version: TemplateVersion) -> None:
    definition = _definition_for(template)
    tokens = document_tokens(version.document, version.subject, version.preheader)
    if definition and definition.get('lane_class') == LANE_AUTH:
        from apps.email.auth_templates import validate_auth_template

        validate_auth_template(definition, version.document, version.subject, version.preheader)
    if definition and definition.get('lane_class') == 'bulk' and 'system.unsubscribe_url' not in tokens:
        raise SendError(400, 'unsubscribe_link_missing')
    if not version.html:
        version.html, version.text = compose(version.document, head=set_head(template.set), preheader=version.preheader)
        version.renderer_version = RENDERER_VERSION
    version.checksum = checksum(version.subject, version.html, version.text)
    version.state = TemplateVersion.STATE_PUBLISHED
    version.published_at = timezone.now()
    version.save()
    TemplateVersion.objects.filter(template=template, state=TemplateVersion.STATE_PUBLISHED).exclude(pk=version.pk).update(
        state=TemplateVersion.STATE_ARCHIVED
    )
    template.active_version = version.number
    template.save(update_fields=['active_version'])


def _next_number(template: EmailTemplate) -> int:
    return (template.versions.order_by('-number').values_list('number', flat=True).first() or 0) + 1


def create_version(
    template: EmailTemplate,
    *,
    document,
    subject: str,
    preheader: str,
    user_id: int | None = None,
) -> TemplateVersion:
    """Validate, compose, and store a draft. The stored HTML is what publishing sends."""
    if not subject:
        raise SendError(400, 'validation_failed', {'subject': ['required']})
    document = _validated_content(_definition_for(template), document, subject, preheader)
    html, text = compose(document, head=set_head(template.set), preheader=preheader)
    return TemplateVersion.objects.create(
        template=template,
        number=_next_number(template),
        state=TemplateVersion.STATE_DRAFT,
        subject=subject[:255],
        preheader=preheader[:255],
        document=document,
        html=html,
        text=text,
        renderer_version=RENDERER_VERSION,
        checksum=checksum(subject, html, text),
        created_by_user_id=user_id,
    )


def library_document_for(template: EmailTemplate, source: LibraryTemplate) -> dict:
    """``source`` adapted to the copy's event, for "Start over from another template"."""
    definition = _definition_for(template) or {}
    return adapt_to_event(source.document, definition)


def _suggested(definition: dict, language: str) -> dict:
    languages = definition['languages']
    return languages.get(language) or languages['en']


def create_copy(
    *,
    company_id: int,
    source: LibraryTemplate,
    definition: dict,
    language: str,
    user_id: int | None = None,
) -> EmailTemplate:
    pack = _suggested(definition, language)
    template = EmailTemplate.objects.create(
        template_key='company.' + secrets.token_hex(6),
        company_id=company_id,
        language=language if language in definition['languages'] else 'en',
        name=definition['label'][:120],
        event_type=definition['event_type'],
        source_key=source.key,
        set=source.set,
    )
    version = create_version(
        template,
        document=adapt_to_event(source.document, definition),
        subject=pack['subject'],
        preheader=pack.get('preheader') or '',
        user_id=user_id,
    )
    publish_version(template, version)
    return template


def _default_source(definition: dict) -> LibraryTemplate:
    source = LibraryTemplate.objects.filter(key=definition.get('default_template') or '', built_in=True).first()
    if source is None:
        raise SendError(503, 'library_not_synced')
    return source


def _static_recipients(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SendError(400, 'validation_failed', {'static_recipients': ['invalid']})
    cleaned = []
    for item in raw:
        if isinstance(item, str):
            email = item.strip()
        elif isinstance(item, dict):
            email = str(item.get('email') or '').strip()
        else:
            raise SendError(400, 'validation_failed', {'static_recipients': ['invalid']})
        if '@' not in email or ' ' in email:
            raise SendError(400, 'validation_failed', {'static_recipients': ['invalid']})
        cleaned.append(email)
    return cleaned


def _language(definition: dict, raw) -> str:
    language = str(raw or '')
    if language and language not in definition['languages']:
        raise SendError(400, 'language_not_available')
    return language


def _mode(raw) -> str:
    mode = str(raw or EmailRule.MODE_HINTS)
    if mode not in {EmailRule.MODE_HINTS, EmailRule.MODE_STATIC}:
        raise SendError(400, 'validation_failed', {'recipient_mode': ['invalid']})
    return mode


def ensure_builtin_rules(company_id: int) -> None:
    for definition in all_definitions():
        if definition['lane_class'] != LANE_AUTH:
            continue
        event_type = definition['event_type']
        if EmailRule.objects.filter(company_id=company_id, event_type=event_type, built_in=True).exists():
            continue
        try:
            with transaction.atomic():
                if EmailRule.objects.filter(company_id=company_id, event_type=event_type, built_in=True).exists():
                    continue
                template = create_copy(
                    company_id=company_id,
                    source=_default_source(definition),
                    definition=definition,
                    language='en',
                )
                EmailRule.objects.create(
                    company_id=company_id,
                    service=definition['owner_service'],
                    event_type=event_type,
                    enabled=True,
                    template=template,
                    language='',
                    recipient_mode=EmailRule.MODE_HINTS,
                    static_recipients=[],
                    built_in=True,
                )
        except IntegrityError:
            continue


def create_rule(company_id: int, data: dict, *, user_id: int | None = None) -> EmailRule:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    event_type = str(data.get('event_type') or '')
    definition = get_definition(event_type)
    if definition is None:
        raise SendError(400, 'event_unknown')
    service = str(data.get('service') or definition['owner_service'])
    if service != definition['owner_service']:
        raise SendError(400, 'event_unknown')
    language = _language(definition, data.get('language'))
    mode = _mode(data.get('recipient_mode'))
    static_recipients = _static_recipients(data.get('static_recipients'))
    enabled = True if data.get('enabled') is None else bool(data.get('enabled'))
    content = data.get('content')
    if not isinstance(content, dict) or content.get('library_id') in (None, ''):
        raise SendError(400, 'validation_failed', {'content': ['library_id_required']})
    source = library_template(company_id, content.get('library_id'))
    with transaction.atomic():
        template = create_copy(
            company_id=company_id,
            source=source,
            definition=definition,
            language=language or 'en',
            user_id=user_id,
        )
        return EmailRule.objects.create(
            company_id=company_id,
            service=service,
            event_type=event_type,
            enabled=enabled,
            template=template,
            language=language,
            recipient_mode=mode,
            static_recipients=static_recipients,
            built_in=False,
        )


def update_rule(rule: EmailRule, data: dict) -> EmailRule:
    if not isinstance(data, dict):
        raise SendError(400, 'validation_failed')
    definition = get_definition(rule.event_type)
    if definition is None:
        raise SendError(400, 'event_unknown')
    if 'enabled' in data and not bool(data.get('enabled')) and rule.built_in:
        raise SendError(409, 'rule_built_in')
    if 'language' in data:
        rule.language = _language(definition, data.get('language'))
    if 'recipient_mode' in data:
        rule.recipient_mode = _mode(data.get('recipient_mode'))
    if 'static_recipients' in data:
        rule.static_recipients = _static_recipients(data.get('static_recipients'))
    if 'enabled' in data:
        rule.enabled = bool(data.get('enabled'))
    rule.save()
    return rule


def delete_rule(rule: EmailRule) -> None:
    if rule.built_in:
        raise SendError(409, 'rule_built_in')
    template = rule.template
    with transaction.atomic():
        rule.delete()
        if not template.rules.exists():
            template.delete()


def _swap_suggested(definition: dict | None, version: TemplateVersion, language: str) -> tuple[str, str]:
    """An unedited suggested subject follows the send language. The body stays as written."""
    if definition is None or language not in definition['languages']:
        return version.subject, version.preheader
    for pack in definition['languages'].values():
        if version.subject == pack['subject'] and (version.preheader or '') == (pack.get('preheader') or ''):
            chosen = definition['languages'][language]
            return chosen['subject'], chosen.get('preheader') or ''
    return version.subject, version.preheader


def default_content(definition: dict, language: str) -> dict:
    """The event's default design, for sends that have no company copy yet."""
    source = _default_source(definition)
    pack = _suggested(definition, language)
    preheader = pack.get('preheader') or ''
    html, text = compose(adapt_to_event(source.document, definition), head=set_head(source.set), preheader=preheader)
    return {
        'subject': pack['subject'],
        'preheader': preheader,
        'html': html,
        'text': text,
        'version': None,
        'language': language if language in definition['languages'] else 'en',
    }


def content_for_template(template: EmailTemplate, language: str) -> dict:
    definition = _definition_for(template)
    version = None
    if template.active_version:
        version = TemplateVersion.objects.filter(
            template=template,
            number=template.active_version,
            state=TemplateVersion.STATE_PUBLISHED,
        ).first()
    requested = language or template.language or 'en'
    if version is None:
        if definition is None:
            raise SendError(404, 'template_not_found')
        return default_content(definition, requested)
    if not version.html:
        version.html, version.text = compose(version.document, head=set_head(template.set), preheader=version.preheader)
        version.save(update_fields=['html', 'text'])
    subject, preheader = _swap_suggested(definition, version, requested)
    swapped = (subject, preheader) != (version.subject, version.preheader)
    return {
        'subject': subject,
        'preheader': preheader,
        'html': version.html,
        'text': version.text,
        'version': version.number,
        'language': requested if swapped else template.language or requested,
    }


def content_for_rule(rule: EmailRule, language: str) -> dict:
    return content_for_template(rule.template, language)


def content_for_event(company_id: int, definition: dict, language: str) -> dict:
    """Direct sends use the company's copy for the event when there is one."""
    copies = EmailTemplate.objects.filter(
        company_id=company_id,
        event_type=definition['event_type'],
        active_version__isnull=False,
    ).order_by('created_at', 'id')
    builtin = EmailRule.objects.filter(company_id=company_id, event_type=definition['event_type'], built_in=True).first()
    template = (
        (builtin.template if builtin else None)
        or copies.filter(language=language).first()
        or copies.filter(language='en').first()
        or copies.first()
    )
    if template is not None:
        return content_for_template(template, language)
    return default_content(definition, language)
