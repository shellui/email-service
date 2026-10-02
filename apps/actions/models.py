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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class EventLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    company_id = models.PositiveIntegerField()
    user_id = models.PositiveIntegerField(null=True, blank=True)
    event_type = models.CharField(max_length=128)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['company_id', '-created_at'], name='actions_eventlog_company_idx'),
        ]
