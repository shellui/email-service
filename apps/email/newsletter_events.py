"""Email rules for email-service's own newsletter events, sent through the local pipeline (no HTTP)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.email.catalog import LANE_TRANSACTIONAL
from apps.email.service import SendError, accept_event

logger = logging.getLogger(__name__)


@dataclass
class LocalPrincipal:
    """email-service itself, for its own ``email.*`` events only."""

    service: str = 'email'
    is_service: bool = False
    is_staff: bool = False

    def allows_company(self, company_id: int) -> bool:
        return True

    def allows_lane(self, lane: str) -> bool:
        return lane == LANE_TRANSACTIONAL

    def allows_template(self, template_key: str) -> bool:
        return template_key.startswith('email.newsletter.')


def _metadata_text(metadata: dict) -> str:
    return '\n'.join(f'{key}: {value}' for key, value in (metadata or {}).items())


def send_event_email(company_id: int, event_type: str, data: dict) -> None:
    """Run the company's email rules for this event.

    Recipients come only from the rule's static list, never from the event, so a public
    sign-up cannot make email-service mail an address of the visitor's choosing.
    """
    payload = {
        'list_id': data.get('list_id'),
        'list_name': data.get('list_name') or '',
        'subscriber_email': data.get('email') or '',
        'first_name': data.get('first_name') or '',
        'language': data.get('language') or '',
        'metadata_text': _metadata_text(data.get('metadata') or {}),
    }
    try:
        accept_event(
            LocalPrincipal(),
            {'company_id': company_id, 'service': 'email', 'event_type': event_type, 'payload': payload, 'recipients': []},
        )
    except SendError as exc:
        # Log the code only: the payload holds visitor text.
        logger.warning('newsletter event email skipped: %s %s', event_type, exc.code)
