"""Unknown JWT kids fail closed. The current kid and a previous JWKS key still verify."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings
from jwt.algorithms import RSAAlgorithm
from rest_framework.test import APIClient

from apps.authapi.jwks_client import JWKSClient, reset_jwks_client

ISSUER = 'https://id.shellui.com'
AUDIENCE = 'shellui'


def _rsa_jwk(kid: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    public_jwk.update({'kid': kid, 'use': 'sig', 'alg': 'RS256'})
    return private, public_jwk


def _token(private, kid: str | None) -> str:
    payload = {
        'sub': '7',
        'email': 'ada@example.com',
        'company_id': 42,
        'user_metadata': {'is_staff': True, 'email': 'ada@example.com'},
        'exp': int(time.time()) + 600,
        'iss': ISSUER,
        'aud': AUDIENCE,
    }
    headers = {'kid': kid} if kid is not None else None
    return jwt.encode(payload, private, algorithm='RS256', headers=headers)


class JWKSKidTests(TestCase):
    def tearDown(self):
        reset_jwks_client()

    def test_unknown_kid_does_not_fall_back_to_the_only_key(self):
        private, jwk = _rsa_jwk('current')
        client = JWKSClient(static_document={'keys': [jwk]})
        with self.assertRaises(jwt.InvalidTokenError):
            client.get_signing_key(_token(private, 'not-in-the-set'))

    def test_missing_kid_does_not_fall_back_to_the_only_key(self):
        private, jwk = _rsa_jwk('current')
        client = JWKSClient(static_document={'keys': [jwk]})
        with self.assertRaises(jwt.InvalidTokenError):
            client.get_signing_key(_token(private, None))

    def test_current_kid_and_previous_key_both_verify(self):
        current_private, current_jwk = _rsa_jwk('current')
        previous_private, previous_jwk = _rsa_jwk('previous')
        client = JWKSClient(static_document={'keys': [current_jwk, previous_jwk]})
        for private, kid in ((current_private, 'current'), (previous_private, 'previous')):
            token = _token(private, kid)
            signing_key = client.get_signing_key(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=['RS256'],
                audience=AUDIENCE,
                issuer=ISSUER,
            )
            self.assertEqual(claims['sub'], '7')

    def test_a_kid_published_on_refresh_verifies(self):
        previous_private, previous_jwk = _rsa_jwk('previous')
        current_private, current_jwk = _rsa_jwk('current')
        client = JWKSClient(url='https://id.example/.well-known/jwks.json')
        documents = [
            {'keys': [previous_jwk]},
            {'keys': [current_jwk, previous_jwk]},
        ]
        token = _token(current_private, 'current')
        with patch.object(client, '_fetch_document', side_effect=documents) as fetch:
            signing_key = client.get_signing_key(token)
        self.assertEqual(fetch.call_count, 2)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            audience=AUDIENCE,
            issuer=ISSUER,
        )
        self.assertEqual(claims['sub'], '7')
        previous = _token(previous_private, 'previous')
        self.assertEqual(client.get_signing_key(previous).key_id, 'previous')

    def test_empty_jwks_returns_no_key(self):
        private, _jwk = _rsa_jwk('current')
        client = JWKSClient(static_document={'keys': []})
        self.assertIsNone(client.get_signing_key(_token(private, 'current')))

    @override_settings(
        IDENTITY_ISSUER=ISSUER,
        IDENTITY_AUDIENCE=AUDIENCE,
        ALLOW_JWT_HS256_FALLBACK=False,
        JWT_HS256_FALLBACK_SECRET='',
    )
    def test_identity_tokens_authenticate_for_current_and_previous_kids(self):
        current_private, current_jwk = _rsa_jwk('current')
        previous_private, previous_jwk = _rsa_jwk('previous')
        with override_settings(IDENTITY_JWKS_DOCUMENT={'keys': [current_jwk, previous_jwk]}):
            reset_jwks_client()
            api = APIClient()
            for private, kid in ((current_private, 'current'), (previous_private, 'previous')):
                api.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(private, kid)}')
                response = api.get('/api/v1/service-clients')
                self.assertEqual(response.status_code, 200, response.content)
            api.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(current_private, "retired")}')
            rejected = api.get('/api/v1/service-clients')
            self.assertEqual(rejected.status_code, 401, rejected.content)
            api.credentials(HTTP_AUTHORIZATION=f'Bearer {_token(current_private, None)}')
            missing = api.get('/api/v1/service-clients')
            self.assertEqual(missing.status_code, 401, missing.content)
