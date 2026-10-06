"""Outbound Shellui Actions: webhook rules, outbox, and event log."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ActionRule(models.Model):
    ACTION_WEBHOOK = 'webhook'
    ACTION_KIND_CHOICES = [
        (ACTION_WEBHOOK, 'Webhook'),
    ]

    company_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    event_type = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)
    action_kind = models.CharField(max_length=16, choices=ACTION_KIND_CHOICES, default=ACTION_WEBHOOK)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_id', 'event_type', 'name']
        indexes = [
            models.Index(fields=['company_id', 'event_type', 'enabled']),
        ]


class ActionOutbox(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_DELIVERED = 'delivered'
    STATUS_FAILED = 'failed'
    STATUS_DEAD = 'dead'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_id = models.PositiveIntegerField(db_index=True)
    action_rule = models.ForeignKey(
        ActionRule,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='outbox_rows',
    )
    event_type = models.CharField(max_length=128)
    envelope = models.JSONField()
    status = models.CharField(max_length=16, default=STATUS_PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True)
    webhook_id = models.CharField(max_length=64, unique=True)
    target_url = models.URLField(blank=True)
    callback_service = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at']),
        ]


class DeliveryAttempt(models.Model):
    STATUS_SUCCESS = 'success'
    STATUS_FAILURE = 'failure'

    outbox = models.ForeignKey(ActionOutbox, on_delete=models.CASCADE, related_name='delivery_attempts')
    status = models.CharField(max_length=16)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    attempt_number = models.PositiveIntegerField()
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # The retry_webhooks run that made this attempt (no FK: runs are purged after 7 days).
    scheduled_job_run_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class EventLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    # Null for staff-only platform events (``email.scheduled_job.*``).
    company_id = models.PositiveIntegerField(null=True, blank=True)
    user_id = models.PositiveIntegerField(null=True, blank=True)
    event_type = models.CharField(max_length=128)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['company_id', '-created_at'], name='actions_eventlog_company_idx'),
        ]


class ScheduledJobRun(models.Model):
    """
    One run of a scheduled job (``retry_webhooks``, ``sweep_email_queue``, ``purge_expired_data``).

    Written by the management command itself, so runs started by the in-container Celery
    beat and by an external cron are both recorded. Skipped runs (another run held the
    lock) are not stored: they only increment a counter (see ``ScheduledJobCounter``).
    ``purge_expired_data`` deletes rows older than ``scheduled_jobs.RUN_RETENTION_DAYS``.
    """

    TRIGGER_CELERY = 'celery'
    TRIGGER_COMMAND = 'command'
    TRIGGER_CHOICES = [
        (TRIGGER_CELERY, 'Celery beat'),
        (TRIGGER_COMMAND, 'Management command'),
    ]

    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED_LOCKED = 'skipped_locked'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCEEDED, 'Succeeded'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SKIPPED_LOCKED, 'Skipped (lock held)'),
    ]

    id = models.BigAutoField(primary_key=True)
    job = models.CharField(max_length=64)
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # Integer counters per item kind, for example ``webhook_deliveries_succeeded``.
    counts = models.JSONField(default=dict, blank=True)
    # Stable key the admin panel translates (``database_error``, ``redis_error``, …).
    error_key = models.CharField(max_length=32, blank=True)
    error_class = models.CharField(max_length=128, blank=True)
    # Sanitized and truncated: no URL query strings, credentials or tokens.
    error_message = models.CharField(max_length=300, blank=True)
    host = models.CharField(max_length=128, blank=True)
    # Staff-only platform event written when the run finished (no FK: purged separately).
    event_log_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Scheduled job run'
        verbose_name_plural = 'Scheduled job runs'
        ordering = ['-started_at', '-id']
        indexes = [
            models.Index(fields=['job', '-started_at'], name='actions_sjrun_job_idx'),
            models.Index(fields=['started_at'], name='actions_sjrun_started_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.job} #{self.pk} ({self.status})'

    @property
    def request_id(self) -> str:
        """Log correlation id used while the run executes (``[req=…]`` in log lines)."""
        return f'sjr-{self.pk}'


class ScheduledJobState(models.Model):
    """Latest timestamps per job. Survives run retention, so ``last_success_at`` is never lost."""

    job = models.CharField(max_length=64, primary_key=True)
    # Creation time is the reference for ``overdue`` until the first successful run.
    created_at = models.DateTimeField(default=timezone.now)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_skipped_at = models.DateTimeField(null=True, blank=True)
    last_duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Scheduled job state'
        verbose_name_plural = 'Scheduled job states'

    def __str__(self) -> str:
        return self.job


class ScheduledJobCounter(models.Model):
    """
    Monotonic counters for Prometheus (``runs.<status>``, ``items.<kind>``).

    Stored in the database because runs happen in the Celery worker or a cron process,
    while ``/api/v1/metrics`` is served by gunicorn workers.
    """

    job = models.CharField(max_length=64)
    name = models.CharField(max_length=64)
    value = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = 'Scheduled job counter'
        verbose_name_plural = 'Scheduled job counters'
        constraints = [
            models.UniqueConstraint(fields=['job', 'name'], name='actions_sjcounter_job_name_uniq'),
        ]

    def __str__(self) -> str:
        return f'{self.job}:{self.name}={self.value}'
