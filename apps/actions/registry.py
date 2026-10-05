"""Outbound webhook event catalog for email-service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEventType:
    id: str
    label: str
    description: str


_EVENTS = (
    DomainEventType('email.message.sent', 'Message sent', 'The provider accepted the message.'),
    DomainEventType('email.message.delivered', 'Message delivered', 'The recipient server accepted the message.'),
    DomainEventType('email.message.delivery_delayed', 'Delivery delayed', 'Delivery was delayed.'),
    DomainEventType('email.message.bounced', 'Message bounced', 'The message bounced.'),
    DomainEventType('email.message.complained', 'Complaint', 'The recipient reported the message.'),
    DomainEventType('email.message.failed', 'Message failed', 'The message failed before or during handoff.'),
    DomainEventType('email.message.expired', 'Message expired', 'The auth-lane TTL elapsed before handoff.'),
    DomainEventType('email.message.suppressed', 'Message suppressed', 'The address was suppressed.'),
    DomainEventType('email.unsubscribe.created', 'Unsubscribe created', 'A recipient unsubscribed.'),
    DomainEventType('email.newsletter.confirmed', 'Newsletter confirmed', 'Someone confirmed a newsletter subscription.'),
    DomainEventType('email.newsletter.unsubscribed', 'Newsletter unsubscribed', 'Someone left a newsletter.'),
)

_BY_ID = {event.id: event for event in _EVENTS}


def all_event_types() -> list[DomainEventType]:
    return list(_EVENTS)


def is_registered_event(event_id: str) -> bool:
    return event_id in _BY_ID


def get_event_type(event_id: str) -> DomainEventType:
    try:
        return _BY_ID[event_id]
    except KeyError as exc:
        raise ValueError(f'Unknown event type: {event_id!r}') from exc
