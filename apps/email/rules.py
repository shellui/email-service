"""Company email themes and the rule list that decides which mail an event sends."""

from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.email.catalog import LANE_AUTH, all_definitions, get_definition
from apps.email.models import CompanyEmailSettings, EmailRule, EmailTemplate, TemplateVersion
from apps.email.rendering import checksum, document_tokens, render_document
from apps.email.service import SendError
from apps.email.themes import DEFAULT_THEME, is_theme

ALWAYS_AVAILABLE = {'recipient_email'}


def company_theme(company_id: int) -> str:
    row = CompanyEmailSettings.objects.filter(company_id=company_id).first()
    if row and is_theme(row.theme):
        return row.theme
    return DEFAULT_THEME


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


def template_theme(template: EmailTemplate) -> str:
    version = representative_version(template)
    if version and is_theme(version.theme_name):
        return version.theme_name
    if template.company_id is not None:
        return company_theme(template.company_id)
    return DEFAULT_THEME


def templates_using_other_theme(company_id: int) -> int:
    chosen = company_theme(company_id)
    count = 0
    for template in EmailTemplate.objects.filter(company_id=company_id):
        if template_theme(template) != chosen:
            count += 1
    return count


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


def template_summary(template: EmailTemplate, *, company_theme_name: str) -> dict:
    theme = template_theme(template)
    return {
        'id': template.pk,
        'template_key': template.template_key,
        'name': template.name,
        'event_type': template.event_type,
        'language': template.language,
        'company_id': template.company_id,
        'active_version': template.active_version,
        'theme': theme,
        'uses_company_theme': theme == company_theme_name,
    }


def publish_version(template: EmailTemplate, version: TemplateVersion) -> None:
    definition = get_definition(template.template_key) or get_definition(template.event_type or '')
    tokens = document_tokens(version.document, version.subject, version.preheader)
    if definition and definition.get('lane_class') == 'auth':
        from apps.email.auth_templates import validate_auth_template

        validate_auth_template(definition, version.document, version.subject, version.preheader)
    if definition and definition.get('lane_class') == 'bulk' and 'system.unsubscribe_url' not in tokens:
        raise SendError(400, 'unsubscribe_link_missing')
    html, text, renderer = render_document(version.document, version.theme_palette or None, version.theme_name)
    version.html = html
    version.text = text
    version.renderer_version = renderer
    version.checksum = checksum(version.subject, html, text)
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


def create_company_template(
    *,
    company_id: int,
    name: str,
    event_type: str,
    language: str,
    subject: str,
    preheader: str,
    document: dict,
    theme: str,
    user_id: int | None = None,
    publish: bool = True,
) -> EmailTemplate:
    template = EmailTemplate.objects.create(
        template_key='company.' + secrets.token_hex(6),
        company_id=company_id,
        language=language,
        name=(name or '')[:120],
        event_type=event_type or '',
    )
    version = TemplateVersion.objects.create(
        template=template,
        number=1,
        state=TemplateVersion.STATE_DRAFT,
        subject=subject,
        preheader=preheader,
        document=document,
        theme_name=theme if is_theme(theme) else DEFAULT_THEME,
        created_by_user_id=user_id,
    )
    if publish:
        publish_version(template, version)
    return template


def _clone_version(template: EmailTemplate, source: TemplateVersion, theme: str, *, publish: bool) -> TemplateVersion:
    version = TemplateVersion.objects.create(
        template=template,
        number=_next_number(template),
        state=TemplateVersion.STATE_DRAFT,
        subject=source.subject,
        preheader=source.preheader,
        document=source.document,
        theme_name=theme,
        theme_palette=source.theme_palette or {},
        created_by_user_id=source.created_by_user_id,
    )
    if publish:
        publish_version(template, version)
    return version


def apply_theme_to_existing(company_id: int, theme: str) -> int:
    updated = 0
    templates = list(EmailTemplate.objects.filter(company_id=company_id))
    for template in templates:
        published = None
        if template.active_version:
            published = TemplateVersion.objects.filter(
                template=template,
                number=template.active_version,
                state=TemplateVersion.STATE_PUBLISHED,
            ).first()
        latest = template.versions.order_by('-number').first()
        draft = latest if latest and latest.state == TemplateVersion.STATE_DRAFT else None
        changed = False
        if published and published.theme_name != theme:
            _clone_version(template, published, theme, publish=True)
            changed = True
        if draft and draft.theme_name != theme:
            _clone_version(template, draft, theme, publish=False)
            changed = True
        if changed:
            updated += 1
    return updated


def update_company_theme(company_id: int, theme: str, *, apply_to_existing: bool) -> tuple[str, int]:
    if not is_theme(theme):
        raise SendError(400, 'theme_unknown')
    with transaction.atomic():
        updated = apply_theme_to_existing(company_id, theme) if apply_to_existing else 0
        CompanyEmailSettings.objects.update_or_create(company_id=company_id, defaults={'theme': theme})
    return theme, updated


def _company_template(company_id: int, raw_id) -> EmailTemplate:
    try:
        template_id = int(raw_id)
    except (TypeError, ValueError):
        raise SendError(404, 'template_not_found')
    template = EmailTemplate.objects.filter(pk=template_id, company_id=company_id).first()
    if template is None:
        raise SendError(404, 'template_not_found')
    return template


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
    theme = company_theme(company_id)
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
                pack = definition['languages']['en']
                template = create_company_template(
                    company_id=company_id,
                    name=definition['label'],
                    event_type=event_type,
                    language='en',
                    subject=pack['subject'],
                    preheader=pack.get('preheader') or '',
                    document=pack['document'],
                    theme=theme,
                    publish=True,
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
    if not isinstance(content, dict):
        raise SendError(400, 'validation_failed', {'content': ['required']})
    content_mode = content.get('mode')
    if content_mode == 'suggested':
        pack_language = language or 'en'
        pack = definition['languages'][pack_language]
        template = create_company_template(
            company_id=company_id,
            name=definition['label'],
            event_type=event_type,
            language=pack_language,
            subject=pack['subject'],
            preheader=pack.get('preheader') or '',
            document=pack['document'],
            theme=company_theme(company_id),
            user_id=user_id,
            publish=True,
        )
    elif content_mode == 'existing':
        template = _company_template(company_id, content.get('template_id'))
        missing = missing_variables(template, definition)
        if missing:
            raise SendError(400, 'template_variables_mismatch', extra={'missing_variables': missing})
    else:
        raise SendError(400, 'validation_failed', {'content': ['invalid']})
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
    if 'template_id' in data:
        template = _company_template(rule.company_id, data.get('template_id'))
        missing = missing_variables(template, definition)
        if missing:
            raise SendError(400, 'template_variables_mismatch', extra={'missing_variables': missing})
        rule.template = template
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
    rule.delete()


def _matches_pack(version: TemplateVersion, pack: dict) -> bool:
    return version.subject == pack['subject'] and (version.preheader or '') == (pack.get('preheader') or '') and version.document == pack['document']


def content_for_template(template: EmailTemplate, language: str, *, theme_fallback: str | None = None) -> dict:
    """Published content for a company template.

    An unedited suggested document follows ``language`` so a French event still
    uses the French catalog pack until someone edits the template.
    """
    definition = get_definition(template.event_type or '') or get_definition(template.template_key)
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
        pack = definition['languages'].get(requested) or definition['languages'].get('en')
        if pack is None:
            raise SendError(400, 'language_not_available')
        fallback = theme_fallback or (company_theme(template.company_id) if template.company_id else DEFAULT_THEME)
        html, text, _renderer = render_document(pack['document'], None, fallback)
        used = requested if requested in definition['languages'] else 'en'
        return {
            'subject': pack['subject'],
            'preheader': pack.get('preheader') or '',
            'html': html,
            'text': text,
            'version': None,
            'language': used,
        }
    if definition is not None:
        matched = next(
            (code for code, pack in definition['languages'].items() if _matches_pack(version, pack)),
            None,
        )
        if matched and requested in definition['languages'] and requested != matched:
            pack = definition['languages'][requested]
            html, text, _renderer = render_document(pack['document'], version.theme_palette or None, version.theme_name)
            return {
                'subject': pack['subject'],
                'preheader': pack.get('preheader') or '',
                'html': html,
                'text': text,
                'version': version.number,
                'language': requested,
            }
    html, text, _renderer = render_document(version.document, version.theme_palette or None, version.theme_name)
    return {
        'subject': version.subject,
        'preheader': version.preheader,
        'html': html,
        'text': text,
        'version': version.number,
        'language': template.language or requested,
    }


def content_for_rule(rule: EmailRule, language: str) -> dict:
    return content_for_template(rule.template, language, theme_fallback=company_theme(rule.company_id))
