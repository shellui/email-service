"""Inline formatting, lists, and dividers in block documents."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from apps.email.auth_templates import validate_auth_template
from apps.email.blocks import safe_href
from apps.email.rendering import render_document
from apps.email.service import SendError

RICH = {
    'preview': 'Rich',
    'blocks': [
        {'type': 'heading', 'text': 'Hi {{ name }}'},
        {
            'type': 'text',
            'text': 'Read the docs now.',
            'content': [
                {'text': 'Read '},
                {'text': 'the docs', 'bold': True, 'href': 'https://shellui.com/docs'},
                {'text': ' now.\nThanks', 'italic': True, 'underline': True},
            ],
        },
        {
            'type': 'list',
            'ordered': True,
            'items': [
                {'text': 'First'},
                {'text': 'Second', 'content': [{'text': 'Second', 'bold': True}]},
            ],
        },
        {'type': 'divider'},
        {'type': 'list', 'items': [{'text': 'Dot'}]},
        {
            'type': 'footer',
            'text': 'Bad link',
            'content': [{'text': 'Bad link', 'href': 'javascript:alert(1)'}],
        },
    ],
}

AUTH = {
    'lane_class': 'auth',
    'variables': [{'token': 'magic_link_url', 'type': 'url', 'required': True}],
}


def _auth_doc(*blocks):
    return {
        'preview': '',
        'blocks': [
            {'type': 'button', 'text': 'Sign in', 'href': '{{ magic_link_url }}'},
            *blocks,
        ],
    }


class RichRenderTests(SimpleTestCase):
    def test_python_renderer_outputs_marks_lists_and_dividers(self):
        html, text, version = render_document(RICH, None, 'matte')
        self.assertEqual(version, 'shellui-email-3')
        self.assertIn('<a href="https://shellui.com/docs"', html)
        self.assertIn('<strong>the docs</strong>', html)
        self.assertIn('<u><em> now.<br>Thanks</em></u>', html)
        self.assertIn('<ol ', html)
        self.assertIn('<li style="margin:0 0 4px;"><strong>Second</strong></li>', html)
        self.assertIn('<ul ', html)
        self.assertIn('<hr ', html)
        self.assertIn('Hi {{ name }}', html)
        self.assertNotIn('javascript:', html)
        self.assertIn('Bad link', html)
        self.assertIn('Read the docs (https://shellui.com/docs) now.', text)
        self.assertIn('1. First\n2. Second', text)
        self.assertIn('- Dot', text)

    def test_content_wins_over_text(self):
        document = {
            'preview': '',
            'blocks': [{'type': 'text', 'text': 'plain', 'content': [{'text': 'rich'}]}],
        }
        html, text, _version = render_document(document, None, 'barebone')
        self.assertIn('rich', html)
        self.assertNotIn('plain', html)
        self.assertIn('rich', text)

    def test_safe_href(self):
        self.assertEqual(safe_href(' https://a.com '), 'https://a.com')
        self.assertEqual(safe_href('mailto:a@b.com'), 'mailto:a@b.com')
        self.assertEqual(safe_href('{{ system.unsubscribe_url }}'), '{{ system.unsubscribe_url }}')
        self.assertIsNone(safe_href('javascript:alert(1)'))
        self.assertIsNone(safe_href('#'))
        self.assertIsNone(safe_href(''))


@override_settings(EMAIL_AUTH_LINK_HOSTS=['id.shellui.com'])
class RichAuthTests(SimpleTestCase):
    def test_inline_link_to_unknown_host_is_refused(self):
        document = _auth_doc(
            {'type': 'text', 'text': 'Help', 'content': [{'text': 'Help', 'href': 'https://evil.example'}]}
        )
        with self.assertRaises(SendError) as caught:
            validate_auth_template(AUTH, document, 'Sign in', '')
        self.assertEqual(caught.exception.code, 'auth_link_host_not_allowed')

    def test_inline_link_to_allowed_host_or_token_passes(self):
        document = _auth_doc(
            {
                'type': 'list',
                'items': [
                    {'text': 'Help', 'content': [{'text': 'Help', 'href': 'https://id.shellui.com/help'}]},
                    {'text': 'Again', 'content': [{'text': 'Again', 'href': '{{ magic_link_url }}'}]},
                ],
            }
        )
        validate_auth_template(AUTH, document, 'Sign in', '')

    def test_literal_url_hidden_in_content_is_refused(self):
        document = _auth_doc(
            {'type': 'footer', 'text': 'Clean', 'content': [{'text': 'Go to https://evil.example'}]}
        )
        with self.assertRaises(SendError) as caught:
            validate_auth_template(AUTH, document, 'Sign in', '')
        self.assertEqual(caught.exception.code, 'auth_literal_link')
