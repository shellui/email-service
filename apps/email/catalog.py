"""Suggested email templates for identity, storage, and hosting webhook events.

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


def _document(preheader: str, heading: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> dict:
    blocks: list[dict[str, str]] = [{'type': 'heading', 'text': heading}]
    for paragraph in paragraphs:
        blocks.append({'type': 'text', 'text': paragraph})
    if button:
        blocks.append({'type': 'button', 'text': button[0], 'href': button[1]})
    blocks.append({'type': 'footer', 'text': footer})
    return {'preview': preheader, 'blocks': blocks}


def _localized(
    *,
    subject_en: str,
    subject_fr: str,
    pre_en: str,
    pre_fr: str,
    heading_en: str,
    heading_fr: str,
    body_en: list[str],
    body_fr: list[str],
    button_en: tuple[str, str] | None = None,
    button_fr: tuple[str, str] | None = None,
) -> dict[str, dict]:
    footer_en = (
        'Sent by Shellui for {{ company_name|default:"your company" }}. '
        'Reference {{ system.message_id }}.'
    )
    footer_fr = (
        'Envoyé par Shellui pour {{ company_name|default:"votre entreprise" }}. '
        'Référence {{ system.message_id }}.'
    )
    return {
        'en': {
            'subject': subject_en,
            'preheader': pre_en,
            'document': _document(pre_en, heading_en, body_en, button_en, footer_en),
        },
        'fr': {
            'subject': subject_fr,
            'preheader': pre_fr,
            'document': _document(pre_fr, heading_fr, body_fr, button_fr, footer_fr),
        },
    }


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
) -> dict[str, Any]:
    service = key.split('.', 1)[0]
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
    }


_COMPANY = _var('company_name', 'string', example='Acme')


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
                heading_en='Your app is on Shellui Hosting',
                heading_fr='Votre application est sur Shellui Hosting',
                body_en=[
                    '{{ display_name|default:"A new app" }} ({{ name|default:"app" }}) was created for {{ company_name|default:"your company" }}.',
                    'The public slug is {{ slug|default:"pending" }}. You can upload a deployment when the artifact is ready.',
                ],
                body_fr=[
                    '{{ display_name|default:"Une nouvelle application" }} ({{ name|default:"app" }}) a été créée pour {{ company_name|default:"votre entreprise" }}.',
                    'Le slug public est {{ slug|default:"en attente" }}. Vous pouvez publier un déploiement lorsque l\'artefact est prêt.',
                ],
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
                heading_en='Hosted app removed',
                heading_fr='Application hébergée retirée',
                body_en=[
                    '{{ display_name|default:"An app" }} ({{ name|default:"app" }}) and its deployments were removed from {{ company_name|default:"your company" }}.',
                ],
                body_fr=[
                    '{{ display_name|default:"Une application" }} ({{ name|default:"app" }}) et ses déploiements ont été retirés de {{ company_name|default:"votre entreprise" }}.',
                ],
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
                heading_en='Deployment created',
                heading_fr='Déploiement créé',
                body_en=[
                    'A deployment of {{ display_name|default:"your app" }} ({{ app_version|default:"unversioned" }}) was created for {{ company_name|default:"your company" }}. It is not serving traffic yet.',
                ],
                body_fr=[
                    'Un déploiement de {{ display_name|default:"votre application" }} ({{ app_version|default:"sans version" }}) a été créé pour {{ company_name|default:"votre entreprise" }}. Il ne sert pas encore le trafic.',
                ],
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
                heading_en='Deployment did not finish',
                heading_fr='Le déploiement ne s\'est pas terminé',
                body_en=[
                    'The deployment of {{ display_name|default:"your app" }} ({{ app_version|default:"unversioned" }}) for {{ company_name|default:"your company" }} failed.',
                    'Reason: {{ error|default:"unknown_error" }}. The previous active deployment, if any, is still the one serving traffic.',
                ],
                body_fr=[
                    'Le déploiement de {{ display_name|default:"votre application" }} ({{ app_version|default:"sans version" }}) pour {{ company_name|default:"votre entreprise" }} a échoué.',
                    'Motif : {{ error|default:"unknown_error" }}. Le déploiement actif précédent, s\'il existe, continue de servir le trafic.',
                ],
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
                heading_en='Deployment is live',
                heading_fr='Le déploiement est en ligne',
                body_en=[
                    '{{ display_name|default:"Your app" }} {{ app_version|default:"" }} is now the active deployment for {{ company_name|default:"your company" }}.',
                    'Public slug: {{ slug|default:"your slug" }}.',
                ],
                body_fr=[
                    '{{ display_name|default:"Votre application" }} {{ app_version|default:"" }} est maintenant le déploiement actif pour {{ company_name|default:"votre entreprise" }}.',
                    'Slug public : {{ slug|default:"votre slug" }}.',
                ],
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
                heading_en='Sign in to {{ company_name }}',
                heading_fr='Connexion à {{ company_name }}',
                body_en=[
                    'Hello {{ recipient_name|default:"there" }},',
                    'Use the button below to sign in to {{ company_name }}. The link works once and expires soon. If you did not ask for it, you can ignore this message.',
                ],
                body_fr=[
                    'Bonjour {{ recipient_name|default:"" }},',
                    'Utilisez le bouton ci-dessous pour vous connecter à {{ company_name }}. Le lien ne fonctionne qu\'une fois et expire bientôt. Si vous n\'êtes pas à l\'origine de cette demande, ignorez ce message.',
                ],
                button_en=('Sign in', '{{ magic_link_url }}'),
                button_fr=('Se connecter', '{{ magic_link_url }}'),
            ),
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
                heading_en='New group',
                heading_fr='Nouveau groupe',
                body_en=[
                    'The group {{ display_name|default:"(unnamed)" }} was created in {{ company_name|default:"your company" }} (source: {{ source|default:"manual" }}).',
                ],
                body_fr=[
                    'Le groupe {{ display_name|default:"(sans nom)" }} a été créé dans {{ company_name|default:"votre entreprise" }} (source : {{ source|default:"manual" }}).',
                ],
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
                heading_en='Group removed',
                heading_fr='Groupe retiré',
                body_en=[
                    'The group {{ display_name|default:"(unnamed)" }} was removed from {{ company_name|default:"your company" }}.',
                ],
                body_fr=[
                    'Le groupe {{ display_name|default:"(sans nom)" }} a été retiré de {{ company_name|default:"votre entreprise" }}.',
                ],
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
                heading_en='Group membership changed',
                heading_fr='Membres du groupe modifiés',
                body_en=[
                    'Membership of {{ display_name|default:"a group" }} in {{ company_name|default:"your company" }} changed ({{ change|default:"updated" }}).',
                ],
                body_fr=[
                    'Les membres de {{ display_name|default:"un groupe" }} dans {{ company_name|default:"votre entreprise" }} ont changé ({{ change|default:"updated" }}).',
                ],
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
                heading_en='Group updated',
                heading_fr='Groupe mis à jour',
                body_en=[
                    '{{ display_name|default:"A group" }} in {{ company_name|default:"your company" }} was updated. Fields: {{ changed_fields|default:"metadata" }}.',
                ],
                body_fr=[
                    '{{ display_name|default:"Un groupe" }} dans {{ company_name|default:"votre entreprise" }} a été mis à jour. Champs : {{ changed_fields|default:"métadonnées" }}.',
                ],
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
                heading_en='Directory provisioning needs attention',
                heading_fr='Le provisionnement d\'annuaire demande une vérification',
                body_en=[
                    'SCIM could not {{ operation|default:"apply" }} {{ display_name|default:"an entry" }} for {{ company_name|default:"your company" }} because of a conflict.',
                    'Review the group in Shellui admin and in your identity provider, then retry the change from the provider.',
                ],
                body_fr=[
                    'SCIM n\'a pas pu effectuer l\'opération {{ operation|default:"apply" }} pour {{ display_name|default:"une entrée" }} dans {{ company_name|default:"votre entreprise" }} à cause d\'un conflit.',
                    'Vérifiez le groupe dans l\'admin Shellui et chez votre fournisseur d\'identité, puis relancez la modification depuis le fournisseur.',
                ],
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
                heading_en='New SCIM token',
                heading_fr='Nouveau jeton SCIM',
                body_en=[
                    'A SCIM token named {{ token_name|default:"(unnamed)" }} (prefix {{ token_prefix|default:"hidden" }}) was created for {{ company_name|default:"your company" }}.',
                    'If you do not recognize this change, revoke the token in Shellui admin.',
                ],
                body_fr=[
                    'Un jeton SCIM nommé {{ token_name|default:"(sans nom)" }} (préfixe {{ token_prefix|default:"masqué" }}) a été créé pour {{ company_name|default:"votre entreprise" }}.',
                    'Si vous ne reconnaissez pas ce changement, révoquez le jeton dans l\'admin Shellui.',
                ],
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
                heading_en='SCIM token revoked',
                heading_fr='Jeton SCIM révoqué',
                body_en=[
                    'The SCIM token {{ token_name|default:"(unnamed)" }} (prefix {{ token_prefix|default:"hidden" }}) was revoked for {{ company_name|default:"your company" }}. Directory sync using that token will stop.',
                ],
                body_fr=[
                    'Le jeton SCIM {{ token_name|default:"(sans nom)" }} (préfixe {{ token_prefix|default:"masqué" }}) a été révoqué pour {{ company_name|default:"votre entreprise" }}. La synchronisation d\'annuaire qui l\'utilise va s\'arrêter.',
                ],
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
                heading_en='Company access disabled',
                heading_fr='Accès à l\'entreprise désactivé',
                body_en=[
                    'Directory sync disabled company access for {{ recipient_email|default:"a user" }} in {{ company_name|default:"your company" }}. The user account was not deleted.',
                ],
                body_fr=[
                    'La synchronisation d\'annuaire a désactivé l\'accès de {{ recipient_email|default:"un utilisateur" }} à {{ company_name|default:"votre entreprise" }}. Le compte n\'a pas été supprimé.',
                ],
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
                heading_en='Company access enabled',
                heading_fr='Accès à l\'entreprise activé',
                body_en=[
                    '{{ recipient_email|default:"A user" }} now has access to {{ company_name|default:"your company" }} (source: {{ source|default:"scim" }}).',
                ],
                body_fr=[
                    '{{ recipient_email|default:"Un utilisateur" }} a maintenant accès à {{ company_name|default:"votre entreprise" }} (source : {{ source|default:"scim" }}).',
                ],
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
                heading_en='New account',
                heading_fr='Nouveau compte',
                body_en=[
                    'An account for {{ recipient_email|default:"a user" }} was created in {{ company_name|default:"your company" }} (source: {{ source|default:"oauth" }}).',
                ],
                body_fr=[
                    'Un compte pour {{ recipient_email|default:"un utilisateur" }} a été créé dans {{ company_name|default:"votre entreprise" }} (source : {{ source|default:"oauth" }}).',
                ],
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
                heading_en='Account deleted',
                heading_fr='Compte supprimé',
                body_en=[
                    'The account {{ recipient_email|default:"a user" }} was permanently deleted from {{ company_name|default:"your company" }}.',
                ],
                body_fr=[
                    'Le compte {{ recipient_email|default:"un utilisateur" }} a été supprimé définitivement de {{ company_name|default:"votre entreprise" }}.',
                ],
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
                heading_en='Invitation revoked',
                heading_fr='Invitation révoquée',
                body_en=[
                    'The invitation for {{ recipient_email|default:"you" }} to join {{ company_name }} was revoked. Sign-in with that invitation will be refused until someone sends a new one.',
                ],
                body_fr=[
                    'L\'invitation de {{ recipient_email|default:"vous" }} à rejoindre {{ company_name }} a été révoquée. La connexion avec cette invitation sera refusée tant qu\'une nouvelle invitation n\'aura pas été envoyée.',
                ],
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
                heading_en='You are invited to {{ company_name }}',
                heading_fr='Vous êtes invité dans {{ company_name }}',
                body_en=[
                    '{{ inviter_name|default:"A teammate" }} invited {{ recipient_email|default:"you" }} to {{ company_name }}.',
                    'Open the invitation to continue. Access starts when you sign in with this email address.',
                ],
                body_fr=[
                    '{{ inviter_name|default:"Un membre de l\'équipe" }} a invité {{ recipient_email|default:"vous" }} dans {{ company_name }}.',
                    'Ouvrez l\'invitation pour continuer. L\'accès commence lorsque vous vous connectez avec cette adresse e-mail.',
                ],
                button_en=('Open invitation', '{{ invitation_url }}'),
                button_fr=('Ouvrir l\'invitation', '{{ invitation_url }}'),
            ),
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
                heading_en='Profile updated',
                heading_fr='Profil mis à jour',
                body_en=[
                    'The profile for {{ recipient_email|default:"a user" }} in {{ company_name|default:"your company" }} was updated. Fields: {{ changed_fields|default:"profile" }}.',
                ],
                body_fr=[
                    'Le profil de {{ recipient_email|default:"un utilisateur" }} dans {{ company_name|default:"votre entreprise" }} a été mis à jour. Champs : {{ changed_fields|default:"profil" }}.',
                ],
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
                heading_en='Storage bucket ready',
                heading_fr='Compartiment de stockage prêt',
                body_en=[
                    'The bucket {{ bucket_name|default:"company" }} was created for {{ company_name|default:"your company" }}. Files uploaded for this company will live there.',
                ],
                body_fr=[
                    'Le compartiment {{ bucket_name|default:"company" }} a été créé pour {{ company_name|default:"votre entreprise" }}. Les fichiers déposés pour cette entreprise y seront conservés.',
                ],
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
                heading_en='File deleted',
                heading_fr='Fichier supprimé',
                body_en=[
                    '{{ path|default:"A file" }} was deleted from bucket {{ bucket_name|default:"company" }} in {{ company_name|default:"your company" }}.',
                ],
                body_fr=[
                    '{{ path|default:"Un fichier" }} a été supprimé du compartiment {{ bucket_name|default:"company" }} dans {{ company_name|default:"votre entreprise" }}.',
                ],
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
                heading_en='File uploaded',
                heading_fr='Fichier déposé',
                body_en=[
                    '{{ path|default:"A file" }} ({{ mime_type|default:"file" }}) was stored in bucket {{ bucket_name|default:"company" }} for {{ company_name|default:"your company" }}.',
                ],
                body_fr=[
                    '{{ path|default:"Un fichier" }} ({{ mime_type|default:"fichier" }}) a été enregistré dans le compartiment {{ bucket_name|default:"company" }} pour {{ company_name|default:"votre entreprise" }}.',
                ],
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
                        'document': pack['document'],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + '\n',
                encoding='utf-8',
            )
