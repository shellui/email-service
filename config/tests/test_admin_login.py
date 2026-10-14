"""Django admin sign-in and sign-out with host-only session and CSRF cookies."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

User = get_user_model()
PASSWORD = 'long-enough-passphrase'

HOST_COOKIES = dict(
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SESSION_COOKIE_NAME='__Host-sessionid',
    CSRF_COOKIE_NAME='__Host-csrftoken',
    CSRF_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_DOMAIN=None,
    CSRF_COOKIE_DOMAIN=None,
    SESSION_COOKIE_PATH='/',
    CSRF_COOKIE_PATH='/',
    ALLOWED_HOSTS=['testserver'],
)

PLAIN_COOKIES = dict(
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    SESSION_COOKIE_NAME='sessionid',
    CSRF_COOKIE_NAME='csrftoken',
    CSRF_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_DOMAIN=None,
    CSRF_COOKIE_DOMAIN=None,
    SESSION_COOKIE_PATH='/',
    CSRF_COOKIE_PATH='/',
    ALLOWED_HOSTS=['testserver'],
)


def _assert_host_only(test, cookie, *, secure: bool, httponly: bool):
    test.assertEqual(cookie['path'], '/')
    test.assertEqual(cookie['domain'], '')
    test.assertNotIn('Domain=', cookie.output())
    test.assertEqual(bool(cookie['secure']), secure)
    test.assertEqual(bool(cookie['httponly']), httponly)


class AdminLoginTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('admin', 'admin@example.com', PASSWORD)

    def _sign_in(self, *, secure: bool, session_name: str, csrf_name: str):
        client = Client(enforce_csrf_checks=True)
        scheme = 'https' if secure else 'http'
        page = client.get('/admin/login/', secure=secure)
        self.assertEqual(page.status_code, 200)
        self.assertIn(csrf_name, page.cookies)
        _assert_host_only(
            self,
            page.cookies[csrf_name],
            secure=secure,
            httponly=True,
        )
        token = str(page.context['csrf_token'])
        posted = client.post(
            '/admin/login/',
            {
                'username': 'admin',
                'password': PASSWORD,
                'csrfmiddlewaretoken': token,
                'next': '/admin/',
            },
            secure=secure,
            HTTP_REFERER=f'{scheme}://testserver/admin/login/',
        )
        self.assertEqual(posted.status_code, 302, posted.content)
        self.assertEqual(posted.url, '/admin/')
        self.assertIn(session_name, client.cookies)
        _assert_host_only(
            self,
            client.cookies[session_name],
            secure=secure,
            httponly=True,
        )
        index = client.get('/admin/', secure=secure)
        self.assertEqual(index.status_code, 200)
        self.assertContains(index, 'Django administration')
        logged_out = client.post(
            '/admin/logout/',
            {'csrfmiddlewaretoken': str(index.context['csrf_token'])},
            secure=secure,
            HTTP_REFERER=f'{scheme}://testserver/admin/',
        )
        self.assertEqual(logged_out.status_code, 200, logged_out.content)
        self.assertContains(logged_out, 'Logged out')
        again = client.get('/admin/', secure=secure)
        self.assertEqual(again.status_code, 302)
        self.assertIn('/admin/login/', again.url)
        return client

    @override_settings(**HOST_COOKIES)
    def test_secure_host_cookies_still_sign_in_and_out(self):
        self._sign_in(secure=True, session_name='__Host-sessionid', csrf_name='__Host-csrftoken')

    @override_settings(**PLAIN_COOKIES)
    def test_plain_http_cookies_still_sign_in_and_out(self):
        self._sign_in(secure=False, session_name='sessionid', csrf_name='csrftoken')
