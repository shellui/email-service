"""Pages people open from an email: newsletter confirmation and unsubscribe."""

from __future__ import annotations

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from apps.email import newsletters
from apps.email.companies import resolve_company_name
from apps.email.catalog import LANE_BULK
from apps.email.models import NewsletterList, Unsubscribe
from apps.email.unsubscribe import parse_unsubscribe_token

PAGE_COPY = {
    'en': {
        'confirm_heading': 'Confirm your subscription',
        'confirm_body': 'Confirm that you want to receive {list}.',
        'confirm_action': 'Confirm subscription',
        'confirmed_heading': 'You are subscribed',
        'confirmed_body': 'You will receive {list}. Every email has a link to unsubscribe.',
        'expired_heading': 'This link has expired',
        'expired_body': 'Sign up again to get a new confirmation email.',
        'unsubscribe_heading': 'Unsubscribe',
        'unsubscribe_list_body': 'Stop receiving {list}.',
        'unsubscribe_body': 'Stop receiving news emails from {company}.',
        'unsubscribe_body_anonymous': 'Stop receiving these emails.',
        'unsubscribe_action': 'Unsubscribe',
        'unsubscribed_heading': 'You are unsubscribed',
        'unsubscribed_body': 'You will not receive these emails anymore.',
        'invalid_heading': 'This link is not valid',
        'invalid_body': 'Use the link from the latest email you received.',
    },
    'fr': {
        'confirm_heading': 'Confirmez votre inscription',
        'confirm_body': 'Confirmez que vous souhaitez recevoir {list}.',
        'confirm_action': "Confirmer l'inscription",
        'confirmed_heading': 'Vous êtes inscrit',
        'confirmed_body': 'Vous recevrez {list}. Chaque e-mail contient un lien de désinscription.',
        'expired_heading': 'Ce lien a expiré',
        'expired_body': 'Inscrivez-vous à nouveau pour recevoir un nouvel e-mail de confirmation.',
        'unsubscribe_heading': 'Se désinscrire',
        'unsubscribe_list_body': 'Ne plus recevoir {list}.',
        'unsubscribe_body': 'Ne plus recevoir les e-mails d\'actualité de {company}.',
        'unsubscribe_body_anonymous': 'Ne plus recevoir ces e-mails.',
        'unsubscribe_action': 'Se désinscrire',
        'unsubscribed_heading': 'Vous êtes désinscrit',
        'unsubscribed_body': 'Vous ne recevrez plus ces e-mails.',
        'invalid_heading': "Ce lien n'est pas valide",
        'invalid_body': 'Utilisez le lien du dernier e-mail reçu.',
    },
}


def _language(raw: str) -> str:
    code = (raw or '').split('-')[0].lower()
    return code if code in PAGE_COPY else 'en'


def _request_language(request) -> str:
    header = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
    return _language(header.split(',')[0].split(';')[0].strip())


def _page(request, language: str, key: str, *, status: int = 200, action: bool = False, eyebrow: str = '', **values):
    copy = PAGE_COPY[language]
    body_key = values.pop('body_key', f'{key}_body')
    context = {
        'lang': language,
        'eyebrow': eyebrow,
        'heading': copy[f'{key}_heading'],
        'body': copy[body_key].format(**values),
        'action': copy[f'{key}_action'] if action else '',
    }
    response = render(request, 'public/message.html', context, status=status)
    response['Referrer-Policy'] = 'no-referrer'
    response['Cache-Control'] = 'no-store'
    return response


@csrf_exempt
def confirm_page(request, token):
    """GET only shows the button: mail scanners open links, and that must not confirm."""
    if request.method not in ('GET', 'POST'):
        return HttpResponse(status=405)
    row = newsletters.pending_for_token(token)
    if row is None:
        language = _request_language(request)
        return _page(request, language, 'expired', status=404)
    newsletter = row.list
    language = _language(row.language or newsletter.default_language)
    eyebrow = resolve_company_name(newsletter.company_id)
    if request.method == 'GET':
        if row.status == row.STATUS_CONFIRMED:
            return _page(request, language, 'confirmed', eyebrow=eyebrow, list=newsletter.name)
        return _page(request, language, 'confirm', action=True, eyebrow=eyebrow, list=newsletter.name)
    if newsletters.confirm(token) is None:
        return _page(request, language, 'expired', status=404)
    if newsletter.confirmed_redirect_url:
        return HttpResponseRedirect(newsletter.confirmed_redirect_url)
    return _page(request, language, 'confirmed', eyebrow=eyebrow, list=newsletter.name)


@csrf_exempt
def unsubscribe_page(request, token):
    """POST is also the RFC 8058 one-click target that mail clients call."""
    language = _request_language(request)
    parsed = parse_unsubscribe_token(token)
    if parsed is None:
        return _page(request, language, 'invalid', status=404)
    if request.method not in ('GET', 'POST'):
        return HttpResponse(status=405)
    company_id, category, hmac_value = parsed
    company = resolve_company_name(company_id)
    list_id = newsletters.list_id_from_category(category)
    newsletter = None
    if list_id is not None:
        newsletter = NewsletterList.objects.filter(pk=list_id, company_id=company_id).first()
        if newsletter is None:
            return _page(request, language, 'invalid', status=404)
    if request.method == 'GET':
        if newsletter is not None:
            body = {'body_key': 'unsubscribe_list_body', 'list': newsletter.name}
        elif company:
            body = {'company': company}
        else:
            body = {'body_key': 'unsubscribe_body_anonymous'}
        return _page(request, language, 'unsubscribe', action=True, eyebrow=company, **body)
    if newsletter is not None:
        newsletters.unsubscribe_from_list(company_id, newsletter.pk, hmac_value, source='one_click')
    else:
        Unsubscribe.objects.get_or_create(
            company_id=company_id,
            email_hmac=hmac_value,
            category=category,
            defaults={'source': 'one_click'},
        )
        if category == LANE_BULK:
            newsletters.unsubscribe_everywhere([company_id], hmac_value, source='one_click')
        from apps.actions.emit import emit_email_event

        emit_email_event(
            company_id=company_id,
            event_type='email.unsubscribe.created',
            data={'category': category, 'source': 'one_click'},
        )
    return _page(request, language, 'unsubscribed', eyebrow=company)
