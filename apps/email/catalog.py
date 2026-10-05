"""Email events for identity, storage, and hosting: variables, lane, suggested subject and preheader.

Each event names the library design a new copy starts from (``default_template``)
and, when it carries a link, the URL token that ``{{ action_url }}`` becomes
(``link_token``).

Template keys match the sibling event ids on the ``develop`` branch so a service
can post the same ``event_type`` it already emits. Login events are omitted:
identity records ``identity.auth.login.succeeded`` and ``identity.auth.login.failed``
in its event log only (``webhook: false``), so they are not offered as email rules.
"""

from __future__ import annotations

from typing import Any

LANE_AUTH = 'auth'
LANE_TRANSACTIONAL = 'transactional'
LANE_BULK = 'bulk'

LANGUAGES = ('en', 'fr')


def _var(
    token: str,
    type_: str,
    *,
    required: bool = False,
    example: Any = '',
    sensitive: bool = False,
    hosts: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        'token': token,
        'type': type_,
        'required': required,
        'description': f'email.var.{token}',
        'example': example,
        'is_url': type_ == 'url',
    }
    if sensitive:
        item['sensitive'] = True
    if hosts:
        item['allowed_hosts_setting'] = 'EMAIL_AUTH_LINK_HOSTS'
    return item


def _localized(*, subject_en: str, subject_fr: str, pre_en: str, pre_fr: str) -> dict[str, dict]:
    """Suggested subject and preheader. The body comes from the library template a rule copies."""
    return {
        'en': {'subject': subject_en, 'preheader': pre_en},
        'fr': {'subject': subject_fr, 'preheader': pre_fr},
    }


DEFAULT_TEMPLATE = 'barebone.text-only'


def _definition(
    *,
    key: str,
    label: str,
    lane_class: str,
    default_enabled: bool,
    category: str,
    variables: list[dict],
    languages: dict[str, dict],
    default_ttl_seconds: int | None = None,
    description: str = '',
    default_template: str = DEFAULT_TEMPLATE,
) -> dict[str, Any]:
    service = key.split('.', 1)[0]
    link_token = next((item['token'] for item in variables if item['type'] == 'url' and item.get('required')), '')
    return {
        'key': key,
        'event_type': key,
        'label': label,
        'description': description,
        'owner_service': service,
        'lane_class': lane_class,
        'default_lane': lane_class,
        'category': category,
        'default_enabled': default_enabled,
        'default_ttl_seconds': default_ttl_seconds,
        'variables': variables,
        'languages': languages,
        'link_token': link_token,
        'default_template': default_template,
    }


_COMPANY = _var('company_name', 'string', example='Acme')


def broadcast_definition() -> dict[str, Any]:
    """What a broadcast's content may use: the bulk lane and each recipient's name. Not an event."""
    return {
        'key': 'broadcast',
        'event_type': '',
        'label': 'Broadcast',
        'description': '',
        'owner_service': '',
        'lane_class': LANE_BULK,
        'default_lane': LANE_BULK,
        'category': 'bulk',
        'default_enabled': False,
        'default_ttl_seconds': None,
        'variables': [
            _COMPANY,
            _var('first_name', 'string', example='Ada'),
            _var('last_name', 'string', example='Lovelace'),
            _var('recipient_email', 'string', example='ada@example.com'),
        ],
        'languages': {},
        'link_token': '',
        'default_template': DEFAULT_TEMPLATE,
    }


def all_definitions() -> list[dict[str, Any]]:
    """Catalog in stable event-id order."""
    rows = [
        _definition(
            key='hosting.app.created',
            label='Hosted app created',
            description='A hosted app record was created.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='My App'),
                _var('name', 'string', example='my-app'),
                _var('slug', 'string', example='abc12345'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ display_name|default:"Your app" }} is ready to host',
                subject_fr='[Shellui] {{ display_name|default:"Votre application" }} est prête à être hébergée',
                pre_en='A new hosted app was created.',
                pre_fr='Une nouvelle application hébergée a été créée.',
            ),
        ),
        _definition(
            key='hosting.app.deleted',
            label='Hosted app deleted',
            description='A hosted app and its deployments were removed.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='My App'),
                _var('name', 'string', example='my-app'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ display_name|default:"An app" }} was removed',
                subject_fr='[Shellui] {{ display_name|default:"Une application" }} a été retirée',
                pre_en='A hosted app was deleted.',
                pre_fr='Une application hébergée a été supprimée.',
            ),
        ),
        _definition(
            key='hosting.deployment.created',
            label='Deployment created',
            description='A deployment row was created and is waiting for an artifact.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='My App'),
                _var('app_version', 'string', example='1.0.0'),
            ],
            languages=_localized(
                subject_en='[Shellui] Deployment started for {{ display_name|default:"your app" }}',
                subject_fr='[Shellui] Déploiement commencé pour {{ display_name|default:"votre application" }}',
                pre_en='A deployment is waiting for its artifact.',
                pre_fr='Un déploiement attend son artefact.',
            ),
        ),
        _definition(
            key='hosting.deployment.failed',
            label='Deployment failed',
            description='Finalize or extract failed.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=True,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='My App'),
                _var('app_version', 'string', example='1.0.0'),
                _var('error', 'string', example='artifact_extract_failed'),
            ],
            languages=_localized(
                subject_en='[Shellui] Deployment failed for {{ display_name|default:"your app" }}',
                subject_fr='[Shellui] Échec du déploiement de {{ display_name|default:"votre application" }}',
                pre_en='The latest deployment did not finish.',
                pre_fr='Le dernier déploiement ne s\'est pas terminé.',
            ),
        ),
        _definition(
            key='hosting.deployment.succeeded',
            label='Deployment succeeded',
            description='The deployment is active and serving traffic.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='My App'),
                _var('app_version', 'string', example='1.0.0'),
                _var('slug', 'string', example='abc12345'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ display_name|default:"Your app" }} is live',
                subject_fr='[Shellui] {{ display_name|default:"Votre application" }} est en ligne',
                pre_en='The deployment is serving traffic.',
                pre_fr='Le déploiement sert le trafic.',
            ),
        ),
        _definition(
            key='identity.auth.magic_link.requested',
            label='Magic link requested',
            description='Passwordless sign-in link. Direct send on the auth lane.',
            lane_class=LANE_AUTH,
            default_enabled=True,
            category='auth',
            default_ttl_seconds=120,
            variables=[
                _var('company_name', 'string', required=True, example='Acme'),
                _var(
                    'magic_link_url',
                    'url',
                    required=True,
                    sensitive=True,
                    hosts=True,
                    example='https://id.shellui.com/api/v1/magic-link/verify?token=example',
                ),
                _var('recipient_name', 'string', example='Ada'),
            ],
            languages=_localized(
                subject_en='[Shellui] Sign in to {{ company_name }}',
                subject_fr='[Shellui] Connexion à {{ company_name }}',
                pre_en='Your sign-in link for {{ company_name }}.',
                pre_fr='Votre lien de connexion pour {{ company_name }}.',
            ),
            default_template='barebone.activation',
        ),
        _definition(
            key='identity.group.created',
            label='Group created',
            description='A company group was created.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='Engineering'),
                _var('source', 'string', example='manual'),
            ],
            languages=_localized(
                subject_en='[Shellui] Group {{ display_name|default:"created" }} was added',
                subject_fr='[Shellui] Le groupe {{ display_name|default:"nouveau" }} a été ajouté',
                pre_en='A group was created.',
                pre_fr='Un groupe a été créé.',
            ),
        ),
        _definition(
            key='identity.group.deleted',
            label='Group deleted',
            description='A company group was removed.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[_COMPANY, _var('display_name', 'string', example='Engineering')],
            languages=_localized(
                subject_en='[Shellui] Group {{ display_name|default:"removed" }} was removed',
                subject_fr='[Shellui] Le groupe {{ display_name|default:"retiré" }} a été retiré',
                pre_en='A group was deleted.',
                pre_fr='Un groupe a été supprimé.',
            ),
        ),
        _definition(
            key='identity.group.membership_changed',
            label='Group membership changed',
            description='Members or nested groups were added or removed.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='Engineering'),
                _var('change', 'string', example='members_replaced'),
            ],
            languages=_localized(
                subject_en='[Shellui] Membership changed in {{ display_name|default:"a group" }}',
                subject_fr='[Shellui] Membres modifiés dans {{ display_name|default:"un groupe" }}',
                pre_en='Group membership changed.',
                pre_fr='Les membres d\'un groupe ont changé.',
            ),
        ),
        _definition(
            key='identity.group.updated',
            label='Group updated',
            description='Group metadata changed.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='Engineering'),
                _var('changed_fields', 'string', example='display_name'),
            ],
            languages=_localized(
                subject_en='[Shellui] Group {{ display_name|default:"updated" }} was updated',
                subject_fr='[Shellui] Le groupe {{ display_name|default:"mis à jour" }} a été mis à jour',
                pre_en='Group details changed.',
                pre_fr='Les détails d\'un groupe ont changé.',
            ),
        ),
        _definition(
            key='identity.scim.provisioning_conflict',
            label='SCIM provisioning conflict',
            description='SCIM returned a conflict, for example a display name collision.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=True,
            category='transactional',
            variables=[
                _COMPANY,
                _var('display_name', 'string', example='Engineering'),
                _var('operation', 'string', example='create'),
            ],
            languages=_localized(
                subject_en='[Shellui] Directory conflict for {{ display_name|default:"a group" }}',
                subject_fr='[Shellui] Conflit d\'annuaire pour {{ display_name|default:"un groupe" }}',
                pre_en='SCIM could not apply a change.',
                pre_fr='SCIM n\'a pas pu appliquer une modification.',
            ),
        ),
        _definition(
            key='identity.scim.token.created',
            label='SCIM token created',
            description='A SCIM bearer token was issued.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('token_name', 'string', example='Okta prod'),
                _var('token_prefix', 'string', example='abc123'),
            ],
            languages=_localized(
                subject_en='[Shellui] SCIM token created for {{ company_name|default:"your company" }}',
                subject_fr='[Shellui] Jeton SCIM créé pour {{ company_name|default:"votre entreprise" }}',
                pre_en='A new SCIM token was issued.',
                pre_fr='Un nouveau jeton SCIM a été émis.',
            ),
        ),
        _definition(
            key='identity.scim.token.revoked',
            label='SCIM token revoked',
            description='A SCIM bearer token was revoked.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('token_name', 'string', example='Okta prod'),
                _var('token_prefix', 'string', example='abc123'),
            ],
            languages=_localized(
                subject_en='[Shellui] SCIM token revoked',
                subject_fr='[Shellui] Jeton SCIM révoqué',
                pre_en='A SCIM token was revoked.',
                pre_fr='Un jeton SCIM a été révoqué.',
            ),
        ),
        _definition(
            key='identity.scim.user.deprovisioned',
            label='SCIM user deprovisioned',
            description='Company access was disabled via SCIM. The account is not deleted.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('recipient_email', 'string', example='ada@acme.com'),
            ],
            languages=_localized(
                subject_en='[Shellui] Access removed for {{ recipient_email|default:"a user" }}',
                subject_fr='[Shellui] Accès retiré pour {{ recipient_email|default:"un utilisateur" }}',
                pre_en='Company access was disabled.',
                pre_fr='L\'accès à l\'entreprise a été désactivé.',
            ),
        ),
        _definition(
            key='identity.scim.user.provisioned',
            label='SCIM user provisioned',
            description='Company access was enabled via SCIM.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('recipient_email', 'string', example='ada@acme.com'),
                _var('source', 'string', example='scim'),
            ],
            languages=_localized(
                subject_en='[Shellui] Access enabled for {{ recipient_email|default:"a user" }}',
                subject_fr='[Shellui] Accès activé pour {{ recipient_email|default:"un utilisateur" }}',
                pre_en='Company access was enabled from the directory.',
                pre_fr='L\'accès à l\'entreprise a été activé depuis l\'annuaire.',
            ),
        ),
        _definition(
            key='identity.user.created',
            label='User account created',
            description='A new user row was created.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('recipient_email', 'string', example='ada@acme.com'),
                _var('source', 'string', example='oauth'),
            ],
            languages=_localized(
                subject_en='[Shellui] Account created for {{ recipient_email|default:"a user" }}',
                subject_fr='[Shellui] Compte créé pour {{ recipient_email|default:"un utilisateur" }}',
                pre_en='A user account was created.',
                pre_fr='Un compte utilisateur a été créé.',
            ),
        ),
        _definition(
            key='identity.user.deleted',
            label='User account deleted',
            description='The user account was permanently deleted.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[_COMPANY, _var('recipient_email', 'string', example='ada@acme.com')],
            languages=_localized(
                subject_en='[Shellui] Account deleted',
                subject_fr='[Shellui] Compte supprimé',
                pre_en='A user account was deleted.',
                pre_fr='Un compte utilisateur a été supprimé.',
            ),
        ),
        _definition(
            key='identity.user.invitation_revoked',
            label='Invitation revoked',
            description='A pending invitation was revoked.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=True,
            category='transactional',
            variables=[
                _var('company_name', 'string', required=True, example='Acme'),
                _var('recipient_email', 'string', example='ada@acme.com'),
            ],
            languages=_localized(
                subject_en='[Shellui] Invitation to {{ company_name }} was revoked',
                subject_fr='[Shellui] L\'invitation à {{ company_name }} a été révoquée',
                pre_en='This invitation is no longer valid.',
                pre_fr='Cette invitation n\'est plus valable.',
            ),
        ),
        _definition(
            key='identity.user.invited',
            label='User invited',
            description='Invitation email. Direct send on the auth lane.',
            lane_class=LANE_AUTH,
            default_enabled=True,
            category='auth',
            default_ttl_seconds=300,
            variables=[
                _var('company_name', 'string', required=True, example='Acme'),
                _var('inviter_name', 'string', example='Grace'),
                _var(
                    'invitation_url',
                    'url',
                    required=True,
                    example='https://app.acme.com/',
                ),
                _var('recipient_email', 'string', example='ada@acme.com'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ inviter_name|default:"Someone" }} invited you to {{ company_name }}',
                subject_fr='[Shellui] {{ inviter_name|default:"Quelqu\'un" }} vous invite dans {{ company_name }}',
                pre_en='You have an invitation to {{ company_name }}.',
                pre_fr='Vous avez une invitation pour {{ company_name }}.',
            ),
            default_template='barebone.welcome',
        ),
        _definition(
            key='identity.user.updated',
            label='User updated',
            description='Profile or SCIM attributes changed. Noisy upstream, off by default.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('recipient_email', 'string', example='ada@acme.com'),
                _var('changed_fields', 'string', example='displayName'),
            ],
            languages=_localized(
                subject_en='[Shellui] Profile updated',
                subject_fr='[Shellui] Profil mis à jour',
                pre_en='Account details changed.',
                pre_fr='Les informations du compte ont changé.',
            ),
        ),
        _definition(
            key='storage.bucket.created',
            label='Company bucket provisioned',
            description='The company bucket was created on first storage access.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[_COMPANY, _var('bucket_name', 'string', example='company')],
            languages=_localized(
                subject_en='[Shellui] Storage is ready for {{ company_name|default:"your company" }}',
                subject_fr='[Shellui] Le stockage est prêt pour {{ company_name|default:"votre entreprise" }}',
                pre_en='The company bucket was created.',
                pre_fr='Le compartiment de l\'entreprise a été créé.',
            ),
        ),
        _definition(
            key='storage.object.deleted',
            label='Object deleted',
            description='An object was removed. Folder placeholders are omitted upstream.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('bucket_name', 'string', example='company'),
                _var('path', 'string', example='docs/report.pdf'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ path|default:"A file" }} was deleted',
                subject_fr='[Shellui] {{ path|default:"Un fichier" }} a été supprimé',
                pre_en='A stored file was removed.',
                pre_fr='Un fichier stocké a été retiré.',
            ),
        ),
        _definition(
            key='storage.object.uploaded',
            label='Object uploaded',
            description='A file was uploaded or overwritten. Off by default because uploads are frequent.',
            lane_class=LANE_TRANSACTIONAL,
            default_enabled=False,
            category='transactional',
            variables=[
                _COMPANY,
                _var('bucket_name', 'string', example='company'),
                _var('path', 'string', example='docs/report.pdf'),
                _var('mime_type', 'string', example='application/pdf'),
            ],
            languages=_localized(
                subject_en='[Shellui] {{ path|default:"A file" }} was uploaded',
                subject_fr='[Shellui] {{ path|default:"Un fichier" }} a été déposé',
                pre_en='A file was stored.',
                pre_fr='Un fichier a été enregistré.',
            ),
        ),
    ]
    return rows


_BY_KEY = {row['key']: row for row in all_definitions()}


def get_definition(template_key: str) -> dict[str, Any] | None:
    return _BY_KEY.get(template_key)


def definition_map() -> dict[str, dict[str, Any]]:
    return _BY_KEY


def export_defaults(root) -> None:
    """Write ``defaults/<service>/<event>/`` JSON files from this catalog."""
    import json
    from pathlib import Path

    root = Path(root)
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                for path in sorted(child.rglob('*'), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                child.rmdir()
    for row in all_definitions():
        folder = root / row['owner_service'] / row['key']
        folder.mkdir(parents=True, exist_ok=True)
        definition = {
            key: row[key]
            for key in (
                'key',
                'owner_service',
                'lane_class',
                'default_lane',
                'category',
                'default_enabled',
                'default_ttl_seconds',
                'variables',
                'link_token',
                'default_template',
            )
        }
        (folder / 'definition.json').write_text(
            json.dumps(definition, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        subjects = {
            language: pack['subject'] for language, pack in row['languages'].items()
        }
        (folder / 'subjects.json').write_text(
            json.dumps(subjects, indent=2, ensure_ascii=False) + '\n',
            encoding='utf-8',
        )
        for language, pack in row['languages'].items():
            (folder / f'{language}.json').write_text(
                json.dumps(
                    {
                        'subject': pack['subject'],
                        'preheader': pack['preheader'],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + '\n',
                encoding='utf-8',
            )
