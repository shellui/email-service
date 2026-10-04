from django.test import SimpleTestCase

from apps.email.substitution import substitute


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
