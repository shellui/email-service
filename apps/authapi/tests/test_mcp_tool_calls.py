"""Happy-path ``tools/call`` for every MCP tool, through ``POST /mcp``.

Each test uses an agent JWT, the Identity revocation snapshot, and mocked mail
providers. Destructive tools still pass the approval gate (state ``approved``,
consume ``consumed: true``) before the view runs.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.actions.emit import store_rule_secret
from apps.actions.models import ActionOutbox, ActionRule, EventLog
from apps.authapi.mcp_identity import reset_revocation_cache_for_tests
from apps.authapi.tests.test_mcp_endpoint import (
    MCP_SETTINGS,
    _agent_token,
    _rpc,
    _snapshot_agent_id,
    make_identity_get_side_effect,
    make_identity_post_side_effect,
)
from apps.email.crypto import decrypt_text, email_hmac, encrypt_json
from apps.email.models import (
    CompanyProvider,
    EmailTemplate,
    LibraryTemplate,
    Message,
    Suppression,
)
from apps.email.newsletters import add_subscriber, create_list
from apps.email.rules import create_rule, create_version
from apps.email.tests.helpers import paragraphs
from apps.providers.registry import fake_provider

COMPANY = 77
OWNER = 'owner@acme.test'
MEMBER = 'member@acme.test'
WEBHOOK = 'https://example.com/hook'

_SETTINGS = {
    **MCP_SETTINGS,
    'EMAIL_FALLBACK_PROVIDER': 'fake',
    'EMAIL_ALLOW_FAKE_PROVIDER': True,
}


def _approving_identity_post(agents_fn):
    """Identity approval state/consume for destructive happy paths. Other posts stay on the default mock."""
    delegated = make_identity_post_side_effect(agents_fn)
    approval_id = str(uuid.uuid4())

    def side_effect(url, **kwargs):
        target = str(url)
        if 'mcp-approval-state' in target:
            ok = MagicMock()
            ok.status_code = 200
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={'status': 'approved', 'id': approval_id})
            return ok
        if 'mcp-approval-consume' in target:
            ok = MagicMock()
            ok.status_code = 200
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={'consumed': True})
            return ok
        if 'mcp-call-logs' in target or 'mcp-approval-requests' in target:
            ok = MagicMock()
            ok.status_code = 201
            ok.raise_for_status = MagicMock()
            ok.json = MagicMock(return_value={'id': 1})
            return ok
        return delegated(url, **kwargs)

    return side_effect


@override_settings(**_SETTINGS)
class McpToolHappyPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fake_provider().sent.clear()
        CompanyProvider.objects.create(
            company_id=COMPANY,
            provider='fake',
            from_email='ops@acme.test',
            bulk_from_email='news@acme.test',
            from_name='Acme',
            credentials_ciphertext=encrypt_json({'api_key': 'company-fake'}, setting='EMAIL_CREDENTIALS_KEY'),
            configured=True,
        )
        source = LibraryTemplate.objects.get(key='barebone.text-only', built_in=True)
        cls.builtin_library_id = source.pk
        from apps.email.library import create_library_template

        owned = create_library_template(COMPANY, {'name': 'Owned design'})
        cls.library_id = owned.pk
        cls.rule = create_rule(
            COMPANY,
            {'event_type': 'hosting.deployment.failed', 'content': {'library_id': source.pk}},
        )
        cls.template_id = cls.rule.template_id
        published = cls.rule.template.versions.get(number=cls.rule.template.active_version)
        draft = create_version(
            cls.rule.template,
            document=published.document,
            subject=published.subject,
            preheader=published.preheader,
            translations=published.translations,
            theme=published.theme,
        )
        cls.draft_number = draft.number
        cls.version_number = published.number
        cls.unused_template = EmailTemplate.objects.create(
            company_id=COMPANY,
            template_key=f'company.{COMPANY}.mcp.unused',
            name='Unused copy',
            event_type='',
            kind=EmailTemplate.KIND_EVENT,
            language='en',
            set='barebone',
        )
        cls.message = Message.objects.create(
            company_id=COMPANY,
            service='hosting',
            template_key='hosting.deployment.failed',
            language='en',
            lane='transactional',
            status=Message.STATUS_QUEUED,
            to_email='reader@acme.test',
            email_hmac=email_hmac('reader@acme.test'),
            accepted_at=timezone.now(),
        )
        cls.suppression = Suppression.objects.create(
            company_id=COMPANY,
            email_hmac=email_hmac('blocked@acme.test'),
            email_masked='b***@acme.test',
            reason=Suppression.REASON_MANUAL,
        )
        cls.newsletter = create_list(COMPANY, {'name': 'MCP audit list'}, user_id=1)
        _outcome, subscriber = add_subscriber(
            cls.newsletter, {'email': MEMBER, 'mode': 'consented'}
        )
        cls.subscriber_id = subscriber.pk
        from apps.email.broadcasts import create_broadcast

        cls.broadcast = create_broadcast(
            COMPANY,
            {
                'name': 'MCP audit broadcast',
                'source_key': 'barebone.product-update',
                'audience': {'mode': 'newsletter', 'list_id': cls.newsletter.pk},
            },
            user_id=1,
        )
        cls.action_rule = ActionRule.objects.create(
            company_id=COMPANY,
            name='Audit hook',
            event_type='email.message.sent',
            config={'url': WEBHOOK, 'secret': store_rule_secret('mcp-audit-secret-value')},
        )
        cls.delivery = ActionOutbox.objects.create(
            company_id=COMPANY,
            action_rule=cls.action_rule,
            event_type='email.message.sent',
            envelope={'type': 'email.message.sent'},
            status=ActionOutbox.STATUS_FAILED,
            last_error='timeout',
            webhook_id=f'mcp-audit-{uuid.uuid4().hex}',
            target_url=WEBHOOK,
        )
        cls.event_log = EventLog.objects.create(
            company_id=COMPANY,
            event_type='email.message.sent',
            data={'source': 'mcp-audit'},
        )

    def setUp(self):
        from apps.authapi.mcp_views import reset_mcp_server_cache
        from apps.authapi.tests import test_mcp_endpoint as endpoint_tests

        endpoint_tests._SNAPSHOT_AGENT_ID = str(uuid.uuid4())
        reset_mcp_server_cache()
        reset_revocation_cache_for_tests()
        self.client = self.client_class()
        fake_provider().sent.clear()
        fake_provider().fail_code = ''

        def _agents_fn():
            return [{'agent_id': _snapshot_agent_id(), 'company_id': COMPANY}]

        self._agents_fn = _agents_fn
        self.revocation_patcher = patch(
            'apps.authapi.mcp_identity.requests.get',
            side_effect=make_identity_get_side_effect(_agents_fn),
        )
        self.revocation_patcher.start()
        self.identity_post_patcher = patch(
            'apps.authapi.mcp_identity.requests.post',
            side_effect=make_identity_post_side_effect(_agents_fn),
        )
        self.mock_identity_post = self.identity_post_patcher.start()

    def tearDown(self):
        self.revocation_patcher.stop()
        self.identity_post_patcher.stop()

    def _token(self, *, access: str, destructive: bool) -> str:
        token, _agent_id = _agent_token(
            access=access,
            read_level='sensitive',
            company_id=COMPANY,
            email=OWNER,
            mcp_destructive=destructive,
        )
        return token

    def _call(self, spec: dict) -> str:
        if spec.get('destructive'):
            self.mock_identity_post.side_effect = _approving_identity_post(self._agents_fn)
        arguments = {'path': spec['path'], 'method': spec['method']}
        path_params = spec.get('path_params')
        if path_params:
            arguments['path_params'] = path_params(self) if callable(path_params) else path_params
        body = spec.get('body')
        if body is not None:
            arguments['body'] = body(self) if callable(body) else body
        query = spec.get('query')
        if query is not None:
            arguments['query'] = query(self) if callable(query) else query
        response = _rpc(
            self.client,
            self._token(access=spec.get('access', 'read_only'), destructive=bool(spec.get('destructive'))),
            'tools/call',
            {'name': spec['tool'], 'arguments': arguments},
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn('error', payload, payload)
        result = payload['result']
        self.assertFalse(result.get('isError'), result)
        return result['content'][0]['text']

    def _run(self, spec: dict) -> None:
        text = self._call(spec)
        needle = spec.get('needle')
        if needle:
            self.assertIn(needle, text, text)
        check = spec.get('check')
        if check:
            check(self, text)


def _check_export(test, text):
    data = json.loads(text)
    test.assertEqual(data['content_type'], 'text/csv')
    test.assertIn('email', data['csv'])
    test.assertIn(MEMBER, data['csv'])
    safe, _agent_id = _agent_token(
        access='read_only', read_level='safe', company_id=COMPANY, email=OWNER
    )
    response = _rpc(
        test.client,
        safe,
        'tools/call',
        {
            'name': 'email_export_newsletter_subscribers',
            'arguments': {
                'path': '/api/v1/newsletters/{newsletter_id}/subscribers.csv',
                'path_params': {'newsletter_id': str(test.newsletter.pk)},
                'method': 'GET',
            },
        },
    )
    test.assertEqual(response.status_code, 200, response.content)
    masked = response.json()['result']['content'][0]['text']
    test.assertNotIn(MEMBER, masked)


def _check_action_create(test, text):
    test.assertNotIn('mcp-create-secret-value', text)
    data = json.loads(text)
    test.assertEqual(data.get('secret'), '[redacted]')
    stored = ActionRule.objects.get(pk=data['id'])
    secret = decrypt_text(stored.config['secret'][4:])
    test.assertNotIn(secret, text)
    test.assertTrue(secret)


def _check_rotate(test, text):
    data = json.loads(text)
    test.assertEqual(data.get('secret'), '[redacted]')
    stored = ActionRule.objects.get(pk=test.action_rule.pk)
    secret = decrypt_text(stored.config['secret'][4:])
    test.assertNotEqual(secret, 'mcp-audit-secret-value')
    test.assertNotIn(secret, text)


def _check_provider_update(test, text):
    test.assertNotIn('re_test_mcp_audit_key', text)
    data = json.loads(text)
    test.assertEqual(data['provider'], 'resend')
    test.assertNotIn('api_key', data)


_NL = lambda test: {'newsletter_id': str(test.newsletter.pk)}
_RULE = lambda test: {'rule_id': str(test.rule.pk)}
_TPL = lambda test: {'template_id': str(test.template_id)}
_LIB = lambda test: {'library_id': str(test.library_id)}
_BCAST = lambda test: {'broadcast_id': str(test.broadcast.pk)}
_ACTION = lambda test: {'id': str(test.action_rule.pk)}

SPECS: list[dict] = [
    {'tool': 'email_get_catalog', 'method': 'GET', 'path': '/api/v1/catalog', 'needle': 'events'},
    {'tool': 'email_list_library', 'method': 'GET', 'path': '/api/v1/library', 'needle': 'Owned design'},
    {
        'tool': 'email_get_library',
        'method': 'GET',
        'path': '/api/v1/library/{library_id}',
        'path_params': _LIB,
        'needle': 'Owned design',
    },
    {
        'tool': 'email_create_library',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/library',
        'body': {'name': 'Created design'},
        'needle': 'Created design',
    },
    {
        'tool': 'email_replace_library',
        'access': 'full',
        'method': 'PUT',
        'path': '/api/v1/library/{library_id}',
        'path_params': _LIB,
        'body': {'name': 'Renamed design'},
        'needle': 'Renamed design',
    },
    {
        'tool': 'email_delete_library',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/library/{library_id}',
        'path_params': _LIB,
        'needle': '"result": null',
    },
    {'tool': 'email_list_templates', 'method': 'GET', 'path': '/api/v1/templates', 'needle': 'templates'},
    {
        'tool': 'email_get_templates',
        'method': 'GET',
        'path': '/api/v1/templates/{template_id}',
        'path_params': _TPL,
        'needle': 'hosting.deployment.failed',
    },
    {
        'tool': 'email_delete_templates',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/templates/{template_id}',
        'path_params': lambda test: {'template_id': str(test.unused_template.pk)},
        'needle': '"result": null',
    },
    {
        'tool': 'email_list_template_versions',
        'method': 'GET',
        'path': '/api/v1/templates/{template_id}/versions',
        'path_params': _TPL,
        'needle': 'versions',
    },
    {
        'tool': 'email_get_template_versions',
        'method': 'GET',
        'path': '/api/v1/templates/{template_id}/versions/{number}',
        'path_params': lambda test: {'template_id': str(test.template_id), 'number': str(test.version_number)},
        'needle': 'published',
    },
    {
        'tool': 'email_create_template_versions',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/templates/{template_id}/versions',
        'path_params': _TPL,
        'body': lambda test: {'subject': 'MCP draft subject', 'document': paragraphs('MCP draft')},
        'needle': 'draft',
    },
    {
        'tool': 'email_publish_template_versions',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/templates/{template_id}/versions/{number}/publish',
        'path_params': lambda test: {'template_id': str(test.template_id), 'number': str(test.draft_number)},
        'needle': 'published',
    },
    {
        'tool': 'email_render_preview_templates',
        'method': 'POST',
        'path': '/api/v1/templates/{template_id}/render-preview',
        'path_params': _TPL,
        'body': {},
        'needle': 'html',
    },
    {
        'tool': 'email_send_test_templates',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/templates/{template_id}/send-test',
        'path_params': _TPL,
        'body': lambda test: {'subject': 'MCP test subject', 'document': paragraphs('MCP test')},
        'needle': 'sent',
    },
    {'tool': 'email_list_rules', 'method': 'GET', 'path': '/api/v1/rules', 'needle': 'hosting.deployment.failed'},
    {
        'tool': 'email_get_rules',
        'method': 'GET',
        'path': '/api/v1/rules/{rule_id}',
        'path_params': _RULE,
        'needle': 'hosting.deployment.failed',
    },
    {
        'tool': 'email_create_rules',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/rules',
        'body': lambda test: {
            'event_type': 'hosting.deployment.succeeded',
            'content': {'library_id': test.builtin_library_id},
        },
        'needle': 'hosting.deployment.succeeded',
    },
    {
        'tool': 'email_update_rules',
        'access': 'full',
        'method': 'PATCH',
        'path': '/api/v1/rules/{rule_id}',
        'path_params': _RULE,
        'body': {'language': 'fr'},
        'needle': '"language": "fr"',
    },
    {
        'tool': 'email_delete_rules',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/rules/{rule_id}',
        'path_params': _RULE,
        'needle': '"result": null',
    },
    {'tool': 'email_get_provider', 'method': 'GET', 'path': '/api/v1/provider', 'needle': 'ops@acme.test'},
    {
        'tool': 'email_replace_provider',
        'access': 'full',
        'method': 'PUT',
        'path': '/api/v1/provider',
        'body': {
            'provider': 'resend',
            'from_email': 'ops@acme.test',
            'from_name': 'Acme',
            'credentials': {'api_key': 're_test_mcp_audit_key'},
        },
        'needle': 'resend',
        'check': _check_provider_update,
    },
    {
        'tool': 'email_test_send_provider',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/provider/test-send',
        'body': {'to': OWNER},
        'needle': 'sent',
    },
    {'tool': 'email_get_auth_link_hosts', 'method': 'GET', 'path': '/api/v1/auth-link-hosts', 'needle': 'hosts'},
    {
        'tool': 'email_replace_auth_link_hosts',
        'access': 'full',
        'method': 'PUT',
        'path': '/api/v1/auth-link-hosts',
        'body': {'hosts': ['app.acme.test']},
        'needle': 'app.acme.test',
    },
    {'tool': 'email_get_stats', 'method': 'GET', 'path': '/api/v1/stats', 'needle': 'totals'},
    {'tool': 'email_list_suppressions', 'method': 'GET', 'path': '/api/v1/suppressions', 'needle': 'suppressions'},
    {
        'tool': 'email_create_suppressions',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/suppressions',
        'body': {'email': 'stop@acme.test'},
        'needle': 'email_masked',
    },
    {
        'tool': 'email_delete_suppressions',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/suppressions/{suppression_id}',
        'path_params': lambda test: {'suppression_id': str(test.suppression.pk)},
        'needle': '"result": null',
    },
    {'tool': 'email_list_messages', 'method': 'GET', 'path': '/api/v1/messages', 'needle': 'messages'},
    {
        'tool': 'email_get_messages',
        'method': 'GET',
        'path': '/api/v1/messages/{message_id}',
        'path_params': lambda test: {'message_id': test.message.public_id},
        'needle': 'queued',
    },
    {
        'tool': 'email_cancel_messages',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/messages/{message_id}/cancel',
        'path_params': lambda test: {'message_id': test.message.public_id},
        'needle': 'cancelled',
    },
    {'tool': 'email_list_broadcasts', 'method': 'GET', 'path': '/api/v1/broadcasts', 'needle': 'MCP audit broadcast'},
    {
        'tool': 'email_get_broadcasts',
        'method': 'GET',
        'path': '/api/v1/broadcasts/{broadcast_id}',
        'path_params': _BCAST,
        'needle': 'MCP audit broadcast',
    },
    {
        'tool': 'email_create_broadcasts',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/broadcasts',
        'body': {'name': 'Created broadcast', 'source_key': 'barebone.product-update'},
        'needle': 'Created broadcast',
    },
    {
        'tool': 'email_update_broadcasts',
        'access': 'full',
        'method': 'PATCH',
        'path': '/api/v1/broadcasts/{broadcast_id}',
        'path_params': _BCAST,
        'body': {'name': 'Renamed broadcast'},
        'needle': 'Renamed broadcast',
    },
    {
        'tool': 'email_broadcasts_preview',
        'method': 'POST',
        'path': '/api/v1/broadcasts/{broadcast_id}/preview',
        'path_params': _BCAST,
        'body': {},
        'needle': 'sendable',
    },
    {
        'tool': 'email_broadcasts_send',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/broadcasts/{broadcast_id}/send',
        'path_params': _BCAST,
        'needle': 'queued',
    },
    {
        'tool': 'email_delete_broadcasts',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/broadcasts/{broadcast_id}',
        'path_params': _BCAST,
        'needle': '"result": null',
    },
    {'tool': 'email_list_newsletters', 'method': 'GET', 'path': '/api/v1/newsletters', 'needle': 'MCP audit list'},
    {
        'tool': 'email_get_newsletters',
        'method': 'GET',
        'path': '/api/v1/newsletters/{newsletter_id}',
        'path_params': _NL,
        'needle': 'MCP audit list',
    },
    {
        'tool': 'email_create_newsletters',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/newsletters',
        'body': {'name': 'Created list'},
        'needle': 'Created list',
    },
    {
        'tool': 'email_update_newsletters',
        'access': 'full',
        'method': 'PATCH',
        'path': '/api/v1/newsletters/{newsletter_id}',
        'path_params': _NL,
        'body': {'description': 'Weekly notes'},
        'needle': 'Weekly notes',
    },
    {
        'tool': 'email_rotate_newsletter_key',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/newsletters/{newsletter_id}/rotate-key',
        'path_params': _NL,
        'needle': 'public_key',
    },
    {
        'tool': 'email_list_newsletters_subscribers',
        'method': 'GET',
        'path': '/api/v1/newsletters/{newsletter_id}/subscribers',
        'path_params': _NL,
        'needle': MEMBER,
    },
    {
        'tool': 'email_create_newsletters_subscribers',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/newsletters/{newsletter_id}/subscribers',
        'path_params': _NL,
        'body': {'email': 'added@acme.test', 'mode': 'consented'},
        'needle': 'added',
    },
    {
        'tool': 'email_export_newsletter_subscribers',
        'method': 'GET',
        'path': '/api/v1/newsletters/{newsletter_id}/subscribers.csv',
        'path_params': _NL,
        'needle': MEMBER,
        'check': _check_export,
    },
    {
        'tool': 'email_import_newsletter_subscribers',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/newsletters/{newsletter_id}/subscribers/import',
        'path_params': _NL,
        'body': {'csv': 'email,first_name\nimported@acme.test,Imported\n', 'mode': 'consented'},
        'needle': 'added',
    },
    {
        'tool': 'email_delete_newsletters_subscribers',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/newsletters/{newsletter_id}/subscribers/{subscriber_id}',
        'path_params': lambda test: {
            'newsletter_id': str(test.newsletter.pk),
            'subscriber_id': str(test.subscriber_id),
        },
        'needle': '"result": null',
    },
    {
        'tool': 'email_delete_newsletters',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/newsletters/{newsletter_id}',
        'path_params': _NL,
        'needle': '"result": null',
    },
    {'tool': 'email_list_action_events', 'method': 'GET', 'path': '/api/v1/actions/events', 'needle': 'email.message.sent'},
    {'tool': 'email_list_action_rules', 'method': 'GET', 'path': '/api/v1/actions/rules', 'needle': 'Audit hook'},
    {
        'tool': 'email_get_action_rules',
        'method': 'GET',
        'path': '/api/v1/actions/rules/{id}',
        'path_params': _ACTION,
        'needle': 'Audit hook',
    },
    {
        'tool': 'email_create_action_rules',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/actions/rules',
        'body': {
            'name': 'Created hook',
            'event_type': 'email.message.sent',
            'url': WEBHOOK,
            'secret': 'mcp-create-secret-value',
        },
        'needle': 'Created hook',
        'check': _check_action_create,
    },
    {
        'tool': 'email_update_action_rules',
        'access': 'full',
        'method': 'PATCH',
        'path': '/api/v1/actions/rules/{id}',
        'path_params': _ACTION,
        'body': {'name': 'Renamed hook'},
        'needle': 'Renamed hook',
    },
    {
        'tool': 'email_rotate_rule_secret',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/actions/rules/{id}/rotate-secret',
        'path_params': _ACTION,
        'needle': '[redacted]',
        'check': _check_rotate,
    },
    {
        'tool': 'email_send_test_rule',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/actions/rules/{id}/send-test',
        'path_params': _ACTION,
        'needle': 'queued',
    },
    {
        'tool': 'email_delete_action_rules',
        'access': 'full',
        'destructive': True,
        'method': 'DELETE',
        'path': '/api/v1/actions/rules/{id}',
        'path_params': _ACTION,
        'needle': '"result": null',
    },
    {'tool': 'email_list_action_deliveries', 'method': 'GET', 'path': '/api/v1/actions/deliveries', 'needle': 'deliveries'},
    {
        'tool': 'email_get_action_deliveries',
        'method': 'GET',
        'path': '/api/v1/actions/deliveries/{delivery_id}',
        'path_params': lambda test: {'delivery_id': str(test.delivery.pk)},
        'needle': 'failed',
    },
    {
        'tool': 'email_requeue_action_deliveries',
        'access': 'full',
        'method': 'POST',
        'path': '/api/v1/actions/deliveries/{delivery_id}/requeue',
        'path_params': lambda test: {'delivery_id': str(test.delivery.pk)},
        'needle': 'pending',
    },
    {'tool': 'email_list_action_event_log', 'method': 'GET', 'path': '/api/v1/actions/event-log', 'needle': 'email.message.sent'},
    {
        'tool': 'email_get_action_event_log',
        'method': 'GET',
        'path': '/api/v1/actions/event-log/{id}',
        'path_params': lambda test: {'id': str(test.event_log.pk)},
        'needle': 'mcp-audit',
    },
    {
        'tool': 'email_list_action_event_log_types',
        'method': 'GET',
        'path': '/api/v1/actions/event-log/types',
        'needle': 'email.message.sent',
    },
    {
        'tool': 'email_get_action_event_log_retention',
        'method': 'GET',
        'path': '/api/v1/actions/event-log/retention',
        'needle': 'retention_days',
    },
]


def _bind(spec: dict):
    def test(self):
        self._run(spec)

    test.__name__ = f'test_happy_{spec["tool"]}'
    test.__doc__ = f'POST /mcp tools/call {spec["tool"]} succeeds.'
    return test


for _spec in SPECS:
    setattr(McpToolHappyPathTests, f'test_happy_{_spec["tool"]}', _bind(_spec))
