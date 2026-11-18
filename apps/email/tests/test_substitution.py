from django.test import SimpleTestCase

from apps.email.substitution import SubstitutionError, substitute


class HtmlDefaultTests(SimpleTestCase):
    def test_default_written_with_html_quotes_is_substituted(self):
        html = '<p>Hi {{ display_name|default:&quot;there &amp; co&quot; }}, {{ company_name }}</p>'
        out, missing = substitute(html, {'company_name': 'Acme'}, html=True)
        self.assertEqual(out, '<p>Hi there &amp; co, Acme</p>')
        self.assertEqual(missing, [])

    def test_value_wins_over_an_escaped_default(self):
        html = '<a href="{{ app_url|default:&quot;https://shellui.com&quot; }}">Go</a>'
        out, _missing = substitute(html, {'app_url': 'https://app.shellui.com/?a=1&b=2'}, html=True)
        self.assertEqual(out, '<a href="https://app.shellui.com/?a=1&amp;b=2">Go</a>')

    def test_plain_text_keeps_literal_entities_in_defaults(self):
        out, _missing = substitute('Hi {{ name|default:"&quot;x&quot;" }}', {}, html=False)
        self.assertEqual(out, 'Hi &quot;x&quot;')


class AuthUrlDevHostTests(SimpleTestCase):
    def test_http_tailscale_host_allowed_when_debug_flag_on(self):
        link = 'http://100.127.19.94:8001/api/v1/magic-link/verify?token=secret'
        out, missing = substitute(
            '<a href="{{ magic_link_url }}">Sign in</a>',
            {'magic_link_url': link},
            html=True,
            url_tokens={'magic_link_url': {'100.127.19.94'}},
            allow_http_localhost=True,
        )
        self.assertEqual(missing, [])
        self.assertIn('100.127.19.94:8001', out)

    def test_http_tailscale_host_refused_without_debug_flag(self):
        link = 'http://100.127.19.94:8001/api/v1/magic-link/verify?token=secret'
        with self.assertRaises(SubstitutionError) as raised:
            substitute(
                '{{ magic_link_url }}',
                {'magic_link_url': link},
                html=False,
                url_tokens={'magic_link_url': {'100.127.19.94'}},
                allow_http_localhost=False,
            )
        self.assertEqual(raised.exception.code, 'variable_url_not_allowed')

    def test_http_unknown_lan_host_still_refused_in_debug(self):
        link = 'http://10.0.0.5:8001/api/v1/magic-link/verify?token=secret'
        with self.assertRaises(SubstitutionError) as raised:
            substitute(
                '{{ magic_link_url }}',
                {'magic_link_url': link},
                html=False,
                url_tokens={'magic_link_url': {'100.127.19.94'}},
                allow_http_localhost=True,
            )
        self.assertEqual(raised.exception.code, 'variable_url_not_allowed')

    def test_system_assets_url_allows_http_public_host_in_debug(self):
        assets = 'http://100.127.19.94:8004/static/library'
        out, missing = substitute(
            '{{ system.assets_url }}/logo.png',
            {'system.assets_url': assets},
            html=False,
            url_tokens={},
            allow_http_localhost=True,
        )
        self.assertEqual(missing, [])
        self.assertEqual(out, f'{assets}/logo.png')
