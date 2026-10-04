"""Email themes: library colors as roles, repainted by a Shellui theme at compose time."""

from __future__ import annotations

import copy
import json
import re

from django.test import TestCase
from rest_framework.test import APIClient

from apps.authapi.principal import EmailPrincipal
from apps.email.library import SETS, library_dir
from apps.email.models import EmailTemplate, LibraryTemplate
from apps.email.rendering import compose_many
from apps.email.service import SendError
from apps.email.theming import clean_theme, resolve, tokenize

OCEAN = {
    'name': 'ocean',
    'label': 'Ocean',
    'colors': {
        'background': '#f0f7ff',
        'foreground': '#0b1d33',
        'card': '#ffffff',
        'muted': '#e2eefb',
        'muted_foreground': '#4a6380',
        'primary': '#0a66c2',
        'primary_foreground': '#fafcff',
        'border': '#c7dbf2',
    },
}
_RAW_COLOR = re.compile(r'(?<![\w-])(?:color|background-color|border-color):rgb\(')


def _owner(company_id):
    client = APIClient()
    client.force_authenticate(user=EmailPrincipal(user_id=3, company_id=company_id, email='owner@acme.com', is_company_owner=True))
    return client


def _raw_seeds():
    for set_key, _name in SETS:
        for path in sorted((library_dir() / set_key).glob('*.json')):
            yield json.loads(path.read_text(encoding='utf-8'))


class TokenizeTests(TestCase):
    def test_every_library_color_becomes_a_role(self):
        for seed in _raw_seeds():
            with self.subTest(key=seed['key']):
                tokenized = json.dumps(tokenize(copy.deepcopy(seed['document']), seed['set']))
                self.assertIsNone(_RAW_COLOR.search(tokenized))
                self.assertIn('var(--email-', tokenized)

    def test_tokenized_designs_compose_exactly_as_before_without_a_theme(self):
        seeds = [next(seed for seed in _raw_seeds() if seed['set'] == set_key) for set_key, _name in SETS]
        items = [{'document': seed['document'], 'head': '', 'preheader': seed['preheader']} for seed in seeds]
        tokenized = [{**item, 'document': tokenize(copy.deepcopy(item['document']), seed['set'])} for item, seed in zip(items, seeds)]
        self.assertEqual(compose_many(tokenized), compose_many(items))

    def test_tokenize_is_idempotent_and_marks_the_outer_cell_as_background(self):
        document = {
            'type': 'doc',
            'content': [
                {'type': 'tableCell', 'attrs': {'style': 'background-color:rgb(243,244,246);margin:0px'}},
                {'type': 'table', 'attrs': {'style': 'background-color:rgb(243,244,246)'}},
                {'type': 'paragraph', 'attrs': {'style': 'color:rgb(1,2,3)'}},
            ],
        }
        once = tokenize(copy.deepcopy(document), 'barebone')
        self.assertEqual(once['content'][0]['attrs']['style'], 'background-color:var(--email-background,rgb(243,244,246));margin:0px')
        self.assertEqual(once['content'][1]['attrs']['style'], 'background-color:var(--email-muted,rgb(243,244,246))')
        self.assertEqual(once['content'][2]['attrs']['style'], 'color:rgb(1,2,3)')
        self.assertEqual(tokenize(copy.deepcopy(once), 'barebone'), once)
        self.assertEqual(tokenize(copy.deepcopy(document), ''), document)

    def test_resolve_uses_the_theme_or_the_design_color(self):
        style = 'color:var(--email-muted-foreground,rgb(123,125,129));background-color:var(--email-primary,rgb(20,23,30))'
        self.assertEqual(resolve(style, {'primary': '#0a66c2'}), 'color:rgb(123,125,129);background-color:#0a66c2')
        self.assertEqual(resolve(style, None), 'color:rgb(123,125,129);background-color:rgb(20,23,30)')
        self.assertEqual(resolve('color:var(--email-unknown,#123456)', OCEAN['colors']), 'color:#123456')

    def test_clean_theme(self):
        self.assertEqual(clean_theme(None), {})
        self.assertEqual(clean_theme({}), {})
        self.assertEqual(clean_theme({'name': 'ocean', 'colors': {'primary': '#0A66C2'}}), {'name': 'ocean', 'label': 'ocean', 'colors': {'primary': '#0a66c2'}})
        for bad in (
            'ocean',
            {'name': 'ocean', 'colors': {}},
            {'name': '', 'colors': {'primary': '#0a66c2'}},
            {'name': 'ocean', 'colors': {'primary': 'red'}},
            {'name': 'ocean', 'colors': {'primary': 'rgb(1,2,3);x:url(a)'}},
            {'name': 'ocean', 'colors': {'sidebar': '#0a66c2'}},
        ):
            with self.subTest(bad=bad), self.assertRaises(SendError) as raised:
                clean_theme(bad)
            self.assertEqual(raised.exception.field_errors, {'theme': ['invalid']})


class ThemeApiTests(TestCase):
    def setUp(self):
        self.owner = _owner(40)

    def _library_id(self, key):
        return LibraryTemplate.objects.get(key=key).pk

    def _rule(self, library_id, theme=None):
        content = {'library_id': library_id}
        if theme is not None:
            content['theme'] = theme
        response = self.owner.post(
            '/api/v1/rules?company_id=40',
            {'event_type': 'hosting.deployment.failed', 'content': content},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        return EmailTemplate.objects.get(pk=response.json()['template_id'])

    def test_built_ins_are_tokenized_and_keep_their_colors(self):
        row = LibraryTemplate.objects.get(key='barebone.welcome')
        self.assertIn('var(--email-primary,rgb(20,23,30))', json.dumps(row.document))
        self.assertNotIn('var(', row.html)
        self.assertIn('rgb(20,23,30)', row.html)
        self.assertEqual(self.owner.get(f'/api/v1/library/{row.pk}?company_id=40').json()['theme'], {})

    def test_a_new_copy_takes_the_creators_theme(self):
        template = self._rule(self._library_id('barebone.welcome'), OCEAN)
        version = template.versions.get(number=template.active_version)
        self.assertEqual(version.theme, OCEAN)
        self.assertIn('#0a66c2', version.html)
        self.assertNotIn('rgb(20,23,30)', version.html)
        self.assertNotIn('var(', version.html)

        listed = self.owner.get(f'/api/v1/templates/{template.pk}/versions').json()['versions']
        self.assertEqual(listed[-1]['theme'], OCEAN)
        kept = self.owner.post(f'/api/v1/templates/{template.pk}/versions', {'document': version.document}, format='json')
        self.assertEqual(kept.status_code, 201, kept.content)
        self.assertEqual(template.versions.get(number=kept.json()['number']).theme, OCEAN)
        cleared = self.owner.post(
            f'/api/v1/templates/{template.pk}/versions', {'document': version.document, 'theme': {}}, format='json'
        )
        draft = template.versions.get(number=cleared.json()['number'])
        self.assertEqual(draft.theme, {})
        self.assertIn('rgb(20,23,30)', draft.html)
        refused = self.owner.post(
            f'/api/v1/templates/{template.pk}/versions',
            {'document': version.document, 'theme': {'name': 'x', 'colors': {'primary': 'blue'}}},
            format='json',
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()['field_errors'], {'theme': ['invalid']})

    def test_library_templates_save_a_theme_that_copies_inherit(self):
        created = self.owner.post(
            '/api/v1/library?company_id=40', {'source_id': self._library_id('matte.welcome'), 'theme': OCEAN}, format='json'
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()['theme'], OCEAN)
        self.assertIn('#0a66c2', created.json()['html'])
        duplicate = self.owner.post('/api/v1/library?company_id=40', {'source_id': created.json()['id']}, format='json')
        self.assertEqual(duplicate.json()['theme'], OCEAN)

        template = self._rule(created.json()['id'], {**OCEAN, 'name': 'other', 'colors': {'primary': '#111111'}})
        self.assertEqual(template.versions.get(number=template.active_version).theme, OCEAN)

        updated = self.owner.put(f'/api/v1/library/{created.json()["id"]}?company_id=40', {'theme': {}}, format='json')
        self.assertEqual(updated.json()['theme'], {})
        self.assertIn('rgb(16,59,5)', updated.json()['html'])

        restarted = self.owner.post(
            f'/api/v1/templates/{template.pk}/versions', {'library_id': self._library_id('studio.promo')}, format='json'
        )
        self.assertEqual(template.versions.get(number=restarted.json()['number']).theme, OCEAN)
