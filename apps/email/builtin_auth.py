"""Built-in auth emails whose copy only Shellui writes.

A company cannot edit these, cannot pick another design for them, cannot add an
email rule on them, and ``POST /api/v1/events`` never sends them. Identity sends
them with ``POST /api/v1/send`` to exactly one address.

``identity.auth.magic_link.staff_blocked`` goes to a staff account that asked for a
magic link. It carries no sign-in link and no token: only a plain link to the
sign-in page the request came from.
"""

from __future__ import annotations

from apps.email.rendering import compose

STAFF_BLOCKED = 'identity.auth.magic_link.staff_blocked'

_FONT = 'font-family:Inter,system-ui,Arial,sans-serif'
_HEADING_STYLE = f'font-size:24px;font-weight:600;line-height:1.3;color:rgb(20,23,30);margin:0 0 16px;{_FONT}'
_PARAGRAPH_STYLE = f'font-size:16px;line-height:1.5;color:rgb(67,69,75);margin:0 0 24px;{_FONT}'
_BUTTON_STYLE = (
    'display:inline-block;background-color:rgb(20,23,30);color:rgb(255,255,255);font-size:16px;'
    f'text-decoration:none;border-radius:0.5rem;padding:14px 24px;{_FONT}'
)

# URL variable each built-in email may link to. The button only shows when it has a value.
LINK_VARIABLE = {STAFF_BLOCKED: 'sign_in_url'}

COPY = {
    STAFF_BLOCKED: {
        'en': {
            'heading': 'No sign-in link for staff accounts',
            'paragraph': (
                'Someone asked for a sign-in link for this address on {{ company_name }}. '
                "For security, staff accounts can't sign in with an email link. "
                'Sign in with your password or SSO instead. '
                "If you didn't ask for this, you can ignore this email."
            ),
            'button': 'Go to sign-in',
        },
        'fr': {
            'heading': 'Pas de lien de connexion pour les comptes staff',
            'paragraph': (
                "Quelqu'un a demandé un lien de connexion pour cette adresse sur {{ company_name }}. "
                'Par sécurité, les comptes staff ne peuvent pas se connecter avec un lien envoyé par e-mail. '
                'Connectez-vous avec votre mot de passe ou le SSO. '
                "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."
            ),
            'button': 'Aller à la connexion',
        },
    },
}


def is_builtin_only(definition: dict | None) -> bool:
    """True for an event whose copy companies never edit or replace."""
    return bool(definition) and not definition.get('company_editable', True)


def builtin_document(definition: dict, language: str, *, with_link: bool = True) -> dict:
    copy = COPY[definition['key']]
    strings = copy.get(language) or copy['en']
    content = [
        {
            'type': 'heading',
            'attrs': {'level': 1, 'style': _HEADING_STYLE},
            'content': [{'type': 'text', 'text': strings['heading']}],
        },
        {
            'type': 'paragraph',
            'attrs': {'style': _PARAGRAPH_STYLE},
            'content': [{'type': 'text', 'text': strings['paragraph']}],
        },
    ]
    link_token = LINK_VARIABLE.get(definition['key']) or ''
    if with_link and link_token and strings.get('button'):
        content.append(
            {
                'type': 'button',
                'attrs': {'href': '{{ ' + link_token + ' }}', 'style': _BUTTON_STYLE},
                'content': [{'type': 'text', 'text': strings['button']}],
            }
        )
    return {'type': 'doc', 'content': content}


def builtin_content(definition: dict, language: str, variables: dict | None = None) -> dict:
    """Subject, preheader, HTML and text from the copy above. Never a company copy.

    With ``variables`` (send time), the button is left out when its link is empty.
    """
    lang = language if language in COPY[definition['key']] else 'en'
    pack = definition['languages'].get(lang) or definition['languages']['en']
    preheader = pack.get('preheader') or ''
    link_token = LINK_VARIABLE.get(definition['key']) or ''
    with_link = True if variables is None else bool(str(variables.get(link_token) or '').strip())
    html, text = compose(builtin_document(definition, lang, with_link=with_link), preheader=preheader)
    return {
        'subject': pack['subject'],
        'preheader': preheader,
        'html': html,
        'text': text,
        'version': None,
        'language': lang,
    }
