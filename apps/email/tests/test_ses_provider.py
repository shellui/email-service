"""Amazon SES adapter and provider settings."""

from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.crypto import decrypt_json
from apps.email.models import CompanyProvider
from apps.providers.base import ProviderMessage
from apps.providers.registry import ACTIVE_PROVIDER_NAMES, get_provider
from apps.providers.ses import SesProvider, sign_request, valid_region

# Computed with botocore SigV4Auth for the same inputs.
SIGNATURE = '23cead8de719d713a7493fe5ae2488f83931b57c1f958b11226c071d3e96cdae'  # gitleaks:allow

CREDS = {
    'access_key_id': 'AKIAIOSFODNN7EXAMPLE',  # gitleaks:allow
    'secret_access_key': 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',  # gitleaks:allow
    'region': 'eu-central-2',
}


def _message(**overrides):
    values = {
        'message_id': 'msg-1',
        'to_email': 'to@example.com',
        'from_email': 'no-reply@acme.com',
        'from_name': 'Acme',
        'subject': 'Hello',
        'html': '<p>Hi</p>',
        'text': 'Hi',
        'idempotency_key': 'msg-1',
        'headers': {
            'List-Unsubscribe': '<https://email.shellui.com/u/x>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
            'Reply-To': 'help@acme.com',
        },
        'tags': {'lane': 'bulk', 'template': 'news.digest'},
    }
    values.update(overrides)
    return ProviderMessage(**values)


def _response(status, body=None, headers=None):
    response = MagicMock()
    response.status_code = status
    response.content = json.dumps(body or {}).encode()
    response.json.return_value = body or {}
    response.headers = headers or {}
    return response


class SesAdapterTests(SimpleTestCase):
    def test_registered_and_active(self):
        self.assertIn('ses', ACTIVE_PROVIDER_NAMES)
        self.assertIsInstance(get_provider('ses'), SesProvider)

    def test_send_uses_api_endpoint_and_maps_fields(self):
        with patch('apps.providers.ses.requests.post', return_value=_response(200, {'MessageId': 'ses-123'})) as post:
            result = SesProvider().send(_message(), {**CREDS, 'configuration_set': 'shellui'})
        self.assertTrue(result.ok)
        self.assertEqual(result.provider_message_id, 'ses-123')
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://email.eu-central-2.amazonaws.com/v2/email/outbound-emails')
        payload = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(payload['FromEmailAddress'], '"Acme" <no-reply@acme.com>')
        self.assertEqual(payload['Destination'], {'ToAddresses': ['to@example.com']})
        self.assertEqual(payload['ReplyToAddresses'], ['help@acme.com'])
        self.assertEqual(payload['ConfigurationSetName'], 'shellui')
        simple = payload['Content']['Simple']
        self.assertEqual(simple['Body']['Html']['Data'], '<p>Hi</p>')
        self.assertEqual(simple['Body']['Text']['Data'], 'Hi')
        names = {header['Name'] for header in simple['Headers']}
        self.assertEqual(names, {'List-Unsubscribe', 'List-Unsubscribe-Post'})
        tags = {tag['Name']: tag['Value'] for tag in payload['EmailTags']}
        self.assertEqual(tags['template'], 'news_digest')
        self.assertEqual(tags['message_id'], 'msg-1')
        auth = post.call_args.kwargs['headers']['Authorization']
        self.assertTrue(auth.startswith('AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/'))
        self.assertIn('/eu-central-2/ses/aws4_request', auth)
        self.assertNotIn(CREDS['secret_access_key'], auth)

    def test_header_injection_is_stripped(self):
        with patch('apps.providers.ses.requests.post', return_value=_response(200, {'MessageId': 'x'})) as post:
            SesProvider().send(_message(headers={'X-Test': 'a\r\nBcc: evil@example.com'}), CREDS)
        payload = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(payload['Content']['Simple']['Headers'], [{'Name': 'X-Test', 'Value': 'aBcc: evil@example.com'}])

    def test_signature_matches_known_value(self):
        headers = sign_request(
            method='POST', host='email.eu-central-2.amazonaws.com', path='/v2/email/outbound-emails', body=b'{}',
            region='eu-central-2', access_key_id=CREDS['access_key_id'],
            secret_access_key=CREDS['secret_access_key'],
            now=datetime.datetime(2026, 10, 25, 12, 0, 0, tzinfo=datetime.UTC),
        )
        self.assertEqual(headers['X-Amz-Date'], '20261025T120000Z')
        self.assertIn('SignedHeaders=content-type;host;x-amz-date', headers['Authorization'])
        self.assertIn(f'Signature={SIGNATURE}', headers['Authorization'])

    def test_error_mapping(self):
        cases = [
            (_response(429, headers={'x-amzn-ErrorType': 'TooManyRequestsException:'}), True, 'provider_rate_limited'),
            (_response(400, headers={'x-amzn-ErrorType': 'LimitExceededException'}), True, 'provider_rate_limited'),
            (_response(503), True, 'provider_unavailable'),
            (_response(500, {'__type': 'InternalFailure'}), True, 'provider_unavailable'),
            (_response(400, headers={'x-amzn-ErrorType': 'MessageRejected'}), False, 'provider_rejected'),
            (_response(400, {'__type': 'com.amazonaws#MailFromDomainNotVerifiedException'}), False, 'provider_rejected'),
            (_response(404, headers={'x-amzn-ErrorType': 'NotFoundException'}), False, 'provider_rejected'),
            (_response(403, headers={'x-amzn-ErrorType': 'InvalidSignatureException'}), False, 'provider_unauthorized'),
            (_response(400, headers={'x-amzn-ErrorType': 'AccountSuspendedException'}), False, 'provider_unauthorized'),
            (_response(400, headers={'x-amzn-ErrorType': 'SendingPausedException'}), False, 'provider_unauthorized'),
        ]
        for response, retryable, code in cases:
            with self.subTest(code=code, status=response.status_code):
                with patch('apps.providers.ses.requests.post', return_value=response):
                    result = SesProvider().send(_message(), CREDS)
                self.assertFalse(result.ok)
                self.assertEqual(result.retryable, retryable)
                self.assertEqual(result.error_code, code)

    def test_network_errors_are_retryable(self):
        for exc, code in ((requests.Timeout(), 'provider_timeout'), (requests.ConnectionError(), 'provider_unreachable')):
            with patch('apps.providers.ses.requests.post', side_effect=exc):
                result = SesProvider().send(_message(), CREDS)
            self.assertTrue(result.retryable)
            self.assertEqual(result.error_code, code)

    def test_missing_or_bad_credentials(self):
        with patch('apps.providers.ses.requests.post') as post:
            self.assertEqual(SesProvider().send(_message(), {}).error_code, 'provider_not_configured')
            bad = SesProvider().send(_message(), {**CREDS, 'region': 'evil.com/x'})
            self.assertEqual(bad.error_code, 'provider_not_configured')
        post.assert_not_called()

    def test_region_pattern(self):
        for region in ('eu-central-2', 'us-east-1', 'ap-southeast-3', 'us-gov-west-1'):
            self.assertTrue(valid_region(region), region)
        for region in ('', 'EU-CENTRAL-2', 'eu-central', 'evil.com', 'eu-central-2.attacker', 'eu central 2'):
            self.assertFalse(valid_region(region), region)


def _owner(company_id):
    client = APIClient()
    client.force_authenticate(
        user=EmailPrincipal(user_id=3, company_id=company_id, email='owner@acme.com', is_company_owner=True)
    )
    return client


@override_settings(DEBUG=True)
class SesProviderSettingsTests(TestCase):
    def test_save_encrypts_and_never_returns_secret(self):
        owner = _owner(51)
        saved = owner.put(
            '/api/v1/provider?company_id=51',
            {
                'provider': 'ses',
                'from_email': 'hello@acme.com',
                'credentials': {
                    'access_key_id': CREDS['access_key_id'],
                    'secret_access_key': CREDS['secret_access_key'],
                    'configuration_set': 'shellui-events',
                },
            },
            format='json',
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        body = saved.json()
        self.assertEqual(body['provider'], 'ses')
        self.assertNotIn(CREDS['secret_access_key'], saved.content.decode())
        self.assertNotIn(CREDS['access_key_id'], saved.content.decode())
        self.assertTrue(body['credentials_hint'].endswith('EKEY'))
        row = CompanyProvider.objects.get(company_id=51)
        self.assertNotIn(CREDS['secret_access_key'], row.credentials_ciphertext)
        stored = decrypt_json(row.credentials_ciphertext, setting='EMAIL_CREDENTIALS_KEY')
        self.assertEqual(stored['region'], 'eu-central-2')
        self.assertEqual(stored['configuration_set'], 'shellui-events')
        fetched = owner.get('/api/v1/provider?company_id=51')
        self.assertNotIn(CREDS['secret_access_key'], fetched.content.decode())

        kept = owner.put(
            '/api/v1/provider?company_id=51',
            {'provider': 'ses', 'from_email': 'hi@acme.com'},
            format='json',
        )
        self.assertEqual(kept.status_code, 200, kept.content)
        row.refresh_from_db()
        stored = decrypt_json(row.credentials_ciphertext, setting='EMAIL_CREDENTIALS_KEY')
        self.assertEqual(stored['secret_access_key'], CREDS['secret_access_key'])

    def test_validation_errors(self):
        owner = _owner(52)
        response = owner.put(
            '/api/v1/provider?company_id=52',
            {
                'provider': 'ses',
                'from_email': 'hello@acme.com',
                'credentials': {'access_key_id': 'bad key', 'region': 'attacker.example', 'configuration_set': 'a b'},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body['error_code'], 'validation_failed')
        self.assertEqual(
            set(body['field_errors']), {'access_key_id', 'secret_access_key', 'region', 'configuration_set'}
        )
        self.assertFalse(CompanyProvider.objects.filter(company_id=52).exists())

