"""Sentry never receives a send body, frame locals, or a query string."""

from __future__ import annotations

import sentry_sdk
from django.test import SimpleTestCase

from config.sentry_scrub import FILTERED, before_send, sentry_options

LINK = 'https://id.shellui.com/api/v1/magic-link/verify?token=secret-token'


class SentryScrubTests(SimpleTestCase):
    def test_options_drop_bodies_and_locals(self):
        options = sentry_options()
        self.assertEqual(options['max_request_body_size'], 'never')
        self.assertFalse(options['include_local_variables'])
        self.assertFalse(options['send_default_pii'])

    def test_before_send_removes_request_data_and_query(self):
        event = {
            'request': {
                'url': 'https://email.shellui.com/api/v1/send?x=1',
                'query_string': 'x=1',
                'data': {'variables': {'magic_link_url': LINK}},
                'headers': {'Authorization': 'Bearer test', 'Referer': LINK, 'Accept': 'application/json'},
            }
        }
        scrubbed = before_send(event)
        self.assertNotIn('secret-token', repr(scrubbed))
        self.assertEqual(scrubbed['request']['url'], 'https://email.shellui.com/api/v1/send')
        self.assertEqual(scrubbed['request']['data'], FILTERED)
        self.assertEqual(scrubbed['request']['headers']['Accept'], 'application/json')

    def test_scrubber_filters_link_keys_in_extra_data(self):
        event = {'extra': {'magic_link_url': LINK, 'html': f'<a href="{LINK}">', 'variables': {'magic_link_url': LINK}}}
        sentry_options()['event_scrubber'].scrub_event(event)
        self.assertNotIn('secret-token', repr(event))

    def test_options_are_accepted_by_the_sdk(self):
        client = sentry_sdk.Client(dsn=None, **sentry_options())
        self.assertEqual(client.options['max_request_body_size'], 'never')
