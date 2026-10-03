"""Queue, templates, provider settings, and service credentials."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class ServiceClient(models.Model):
    """Hashed service API key. The plaintext ``esk_`` value is shown once."""

    service = models.CharField(max_length=64)
    name = models.CharField(max_length=128, blank=True)
    key_prefix = models.CharField(max_length=20, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)
    allowed_lanes = models.JSONField(default=list)
    allowed_template_prefixes = models.JSONField(default=list)
    allowed_company_ids = models.JSONField(null=True, blank=True)
    callback_url = models.URLField(blank=True)
    callback_secret_ciphertext = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['service', 'id']

    def __str__(self) -> str:
        return f'{self.service}:{self.key_prefix}'


class CompanyProvider(models.Model):
    """Per-company sending provider. Credentials are encrypted and never returned."""

    company_id = models.PositiveIntegerField(unique=True)
    provider = models.CharField(max_length=32)
    from_email = models.EmailField()
    from_name = models.CharField(max_length=128, blank=True)
    sending_domain = models.CharField(max_length=255, blank=True)
    bulk_from_email = models.EmailField(blank=True)
    credentials_ciphertext = models.TextField(blank=True)
    credentials_hint = models.CharField(max_length=32, blank=True)
    webhook_ciphertext = models.TextField(blank=True)
    webhook_hint = models.CharField(max_length=32, blank=True)
    configured = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f'{self.company_id}:{self.provider}'


class EmailTemplate(models.Model):
    template_key = models.CharField(max_length=128)
    company_id = models.PositiveIntegerField(null=True, blank=True)
    language = models.CharField(max_length=8)
    name = models.CharField(max_length=120, blank=True)
    event_type = models.CharField(max_length=128, blank=True)
    active_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['template_key', 'company_id', 'language'],
                name='uniq_template_company_lang',
            ),
            models.UniqueConstraint(
                fields=['template_key', 'language'],
                condition=Q(company_id__isnull=True),
                name='uniq_platform_template_lang',
            ),
        ]

    def __str__(self) -> str:
        scope = self.company_id if self.company_id is not None else 'platform'
        return f'{self.template_key}:{self.language}:{scope}'


class TemplateVersion(models.Model):
    STATE_DRAFT = 'draft'
    STATE_PUBLISHED = 'published'
    STATE_ARCHIVED = 'archived'

    template = models.ForeignKey(EmailTemplate, related_name='versions', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    state = models.CharField(max_length=16, default=STATE_DRAFT)
    subject = models.CharField(max_length=255)
    preheader = models.CharField(max_length=255, blank=True)
    document = models.JSONField(default=dict)
    html = models.TextField(blank=True)
    text = models.TextField(blank=True)
    theme_name = models.CharField(max_length=64, default='barebone')
    theme_palette = models.JSONField(default=dict, blank=True)
    renderer_version = models.CharField(max_length=32, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    created_by_user_id = models.PositiveIntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['template', 'number'], name='uniq_template_version_number'),
        ]
        ordering = ['template_id', 'number']


class CompanyEmailSettings(models.Model):
    """The theme new company templates start from. Missing row means barebone."""

    company_id = models.PositiveIntegerField(unique=True)
    theme = models.CharField(max_length=32, default='barebone')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f'{self.company_id}:{self.theme}'


class EmailRule(models.Model):
    """One send instruction. A company may keep several rules on the same event."""

    MODE_HINTS = 'hints'
    MODE_STATIC = 'static'

    company_id = models.PositiveIntegerField(db_index=True)
    service = models.CharField(max_length=64)
    event_type = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)
    template = models.ForeignKey(EmailTemplate, related_name='rules', on_delete=models.PROTECT)
    language = models.CharField(max_length=8, blank=True)
    recipient_mode = models.CharField(max_length=16, default=MODE_HINTS)
    static_recipients = models.JSONField(default=list, blank=True)
    built_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['event_type', 'created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['company_id', 'event_type'],
                condition=Q(built_in=True),
                name='uniq_builtin_email_rule',
            ),
        ]
        indexes = [
            models.Index(fields=['company_id', 'service', 'event_type'], name='email_rule_lookup_idx'),
        ]


class CompanyProfile(models.Model):
    """Display name learned from a caller. Later events can omit ``company_name``."""

    company_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)


class EventSkip(models.Model):
    """An accepted event that did not queue mail."""

    REASON_RULE_DISABLED = 'rule_disabled'
    REASON_NO_RECIPIENTS = 'no_recipients'
    REASON_NO_RULE = 'no_rule'

    company_id = models.PositiveIntegerField(db_index=True)
    service = models.CharField(max_length=64)
    event_type = models.CharField(max_length=128)
    reason = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['company_id', 'created_at'], name='email_skip_company_day_idx'),
        ]


class SendRequest(models.Model):
    service = models.CharField(max_length=64)
    company_id = models.PositiveIntegerField()
    idempotency_key = models.CharField(max_length=200)
    payload_hash = models.CharField(max_length=64)
    response_status = models.PositiveIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['service', 'company_id', 'idempotency_key'],
                name='uniq_send_idempotency',
            ),
        ]


class Message(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENDING = 'sending'
    STATUS_RETRYING = 'retrying'
    STATUS_SENT = 'sent'
    STATUS_DELIVERED = 'delivered'
    STATUS_DELAYED = 'delivery_delayed'
    STATUS_BOUNCED = 'bounced'
    STATUS_COMPLAINED = 'complained'
    STATUS_FAILED = 'failed'
    STATUS_EXPIRED = 'expired'
    STATUS_SUPPRESSED = 'suppressed'
    STATUS_CANCELLED = 'cancelled'

    TERMINAL = {
        STATUS_SENT,
        STATUS_DELIVERED,
        STATUS_DELAYED,
        STATUS_BOUNCED,
        STATUS_COMPLAINED,
        STATUS_FAILED,
        STATUS_EXPIRED,
        STATUS_SUPPRESSED,
        STATUS_CANCELLED,
    }
    PROVIDER_ACCEPTED = {
        STATUS_SENT,
        STATUS_DELIVERED,
        STATUS_DELAYED,
        STATUS_BOUNCED,
        STATUS_COMPLAINED,
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_id = models.PositiveIntegerField(db_index=True)
    service = models.CharField(max_length=64)
    send_request = models.ForeignKey(SendRequest, null=True, blank=True, on_delete=models.SET_NULL)
    template_key = models.CharField(max_length=128)
    template_version = models.PositiveIntegerField(null=True, blank=True)
    language = models.CharField(max_length=8)
    lane = models.CharField(max_length=32)
    status = models.CharField(max_length=32, default=STATUS_QUEUED, db_index=True)
    to_email = models.EmailField()
    to_user_id = models.PositiveIntegerField(null=True, blank=True)
    email_hmac = models.CharField(max_length=64, db_index=True)
    variables_ciphertext = models.TextField(blank=True)
    provider = models.CharField(max_length=32, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    smtp_failover_used = models.BooleanField(default=False)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    event_type = models.CharField(max_length=128, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['lane', 'status', 'next_attempt_at'], name='email_msg_lane_queue_idx'),
            models.Index(fields=['company_id', 'accepted_at'], name='email_msg_company_day_idx'),
        ]

    @property
    def public_id(self) -> str:
        return f'msg_{self.id.hex}'


class MessageEvent(models.Model):
    message = models.ForeignKey(Message, related_name='events', on_delete=models.CASCADE)
    type = models.CharField(max_length=32)
    occurred_at = models.DateTimeField()
    provider_event_id = models.CharField(max_length=128, null=True, blank=True, unique=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['occurred_at', 'id']


class Suppression(models.Model):
    REASON_HARD_BOUNCE = 'hard_bounce'
    REASON_COMPLAINT = 'complaint'
    REASON_MANUAL = 'manual'
    REASON_PROVIDER = 'provider'

    company_id = models.PositiveIntegerField(null=True, blank=True)
    email_hmac = models.CharField(max_length=64)
    email_masked = models.CharField(max_length=255)
    reason = models.CharField(max_length=32)
    lanes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['email_hmac', 'company_id'], name='email_suppression_lookup_idx'),
        ]


class Unsubscribe(models.Model):
    company_id = models.PositiveIntegerField()
    email_hmac = models.CharField(max_length=64)
    category = models.CharField(max_length=32)
    source = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['company_id', 'email_hmac', 'category'],
                name='uniq_unsubscribe',
            ),
        ]


class LaneState(models.Model):
    """A pause is global when ``company_id`` is null, otherwise it covers one company."""

    lane = models.CharField(max_length=32)
    company_id = models.PositiveIntegerField(null=True, blank=True)
    paused = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['lane'],
                condition=Q(company_id__isnull=True),
                name='uniq_global_lane_state',
            ),
            models.UniqueConstraint(
                fields=['lane', 'company_id'],
                condition=Q(company_id__isnull=False),
                name='uniq_company_lane_state',
            ),
        ]


def message_public_id(value: uuid.UUID | str) -> str:
    if isinstance(value, uuid.UUID):
        return f'msg_{value.hex}'
    text = str(value)
    if text.startswith('msg_'):
        return text
    return f'msg_{text.replace("-", "")}'


def parse_message_id(value: str) -> uuid.UUID:
    raw = (value or '').strip()
    if raw.startswith('msg_'):
        raw = raw[4:]
    return uuid.UUID(raw)
