"""OpenAPI shapes for the Shellui Actions admin API. Documentation only."""

from __future__ import annotations

from rest_framework import serializers


class ActionEventSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()


class ActionEventListSerializer(serializers.Serializer):
    events = ActionEventSerializer(many=True)


class ActionRuleConfigSerializer(serializers.Serializer):
    url = serializers.CharField(required=False)
    has_secret = serializers.BooleanField(required=False)
    secret_hint = serializers.CharField(required=False, allow_blank=True)


class ActionRuleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    company_id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    event_type = serializers.CharField()
    enabled = serializers.BooleanField()
    action_kind = serializers.CharField()
    config = ActionRuleConfigSerializer()
    secret = serializers.CharField(required=False)
    created_at = serializers.CharField()
    updated_at = serializers.CharField()


class ActionRuleListSerializer(serializers.Serializer):
    rules = ActionRuleSerializer(many=True)


class ActionRuleCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    event_type = serializers.CharField()
    url = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    enabled = serializers.BooleanField(required=False)
    secret = serializers.CharField(required=False, allow_blank=True)


class ActionRuleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    enabled = serializers.BooleanField(required=False)
    url = serializers.CharField(required=False)


class DeliveryAttemptSerializer(serializers.Serializer):
    attempt_number = serializers.IntegerField()
    status = serializers.CharField()
    http_status = serializers.IntegerField(allow_null=True)
    error_code = serializers.CharField(allow_blank=True)


class DeliverySerializer(serializers.Serializer):
    id = serializers.CharField()
    company_id = serializers.IntegerField()
    action_rule_id = serializers.IntegerField(allow_null=True)
    event_type = serializers.CharField()
    status = serializers.CharField()
    attempt_count = serializers.IntegerField()
    last_error = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField()


class DeliveryDetailSerializer(DeliverySerializer):
    attempts = DeliveryAttemptSerializer(many=True)
    envelope = serializers.JSONField()


class DeliveryListSerializer(serializers.Serializer):
    deliveries = DeliverySerializer(many=True)


class EventLogEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    data = serializers.JSONField()
    created_at = serializers.CharField()


class EventLogListSerializer(serializers.Serializer):
    events = EventLogEntrySerializer(many=True)


class EventLogDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    company_id = serializers.IntegerField(allow_null=True)
    event_type = serializers.CharField()
    data = serializers.JSONField()
    created_at = serializers.CharField()


class EventLogTypesSerializer(serializers.Serializer):
    types = serializers.ListField(child=serializers.CharField())


class EventLogRetentionSerializer(serializers.Serializer):
    retention_days = serializers.IntegerField()
    company_id = serializers.IntegerField()


class ActionTestQueuedSerializer(serializers.Serializer):
    status = serializers.CharField()


class ScheduledJobRunSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    job = serializers.CharField()
    trigger = serializers.ChoiceField(choices=['celery', 'command'])
    status = serializers.ChoiceField(choices=['running', 'succeeded', 'failed'])
    started_at = serializers.CharField(allow_null=True)
    finished_at = serializers.CharField(allow_null=True)
    duration_ms = serializers.IntegerField(allow_null=True)
    counts = serializers.DictField(child=serializers.IntegerField())
    error_key = serializers.CharField(allow_null=True)
    error_class = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    host = serializers.CharField()
    request_id = serializers.CharField()
    event_log_id = serializers.IntegerField(allow_null=True)


class ScheduledJobWindowSerializer(serializers.Serializer):
    succeeded = serializers.IntegerField()
    failed = serializers.IntegerField()


class ScheduledJobEntrySerializer(serializers.Serializer):
    job = serializers.CharField()
    health = serializers.ChoiceField(choices=['healthy', 'overdue', 'failing', 'disabled'])
    overdue = serializers.BooleanField()
    interval_seconds = serializers.IntegerField()
    overdue_after_seconds = serializers.IntegerField()
    last_run = ScheduledJobRunSerializer(allow_null=True)
    last_success_at = serializers.CharField(allow_null=True)
    last_failure_at = serializers.CharField(allow_null=True)
    last_skipped_at = serializers.CharField(allow_null=True)
    last_duration_ms = serializers.IntegerField(allow_null=True)
    last_counts = serializers.DictField(child=serializers.IntegerField())
    next_expected_at = serializers.CharField()
    last_24h = ScheduledJobWindowSerializer()
    skipped_locked_total = serializers.IntegerField()


class LaneWorkerSerializer(serializers.Serializer):
    lane = serializers.ChoiceField(choices=['auth', 'transactional', 'bulk'])
    last_seen_at = serializers.CharField(allow_null=True)
    stale = serializers.BooleanField()


class ScheduledJobsOverviewSerializer(serializers.Serializer):
    generated_at = serializers.CharField()
    scheduler_enabled = serializers.BooleanField()
    redis_reachable = serializers.BooleanField(allow_null=True)
    beat_last_seen_at = serializers.CharField(allow_null=True)
    beat_stale = serializers.BooleanField()
    jobs = ScheduledJobEntrySerializer(many=True)
    workers_enabled = serializers.BooleanField()
    workers = LaneWorkerSerializer(many=True)


class ScheduledJobRunListSerializer(serializers.Serializer):
    job = serializers.CharField()
    results = ScheduledJobRunSerializer(many=True)


class ScheduledJobDeliveryAttemptSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    delivery_id = serializers.CharField()
    company_id = serializers.IntegerField()
    event_type = serializers.CharField()
    status = serializers.CharField()
    http_status = serializers.IntegerField(allow_null=True)
    error_code = serializers.CharField()
    attempt_number = serializers.IntegerField()
    duration_ms = serializers.IntegerField(allow_null=True)
    created_at = serializers.CharField()


class ScheduledJobRunDetailSerializer(ScheduledJobRunSerializer):
    webhook_delivery_attempts = ScheduledJobDeliveryAttemptSerializer(many=True)
    webhook_delivery_attempts_truncated = serializers.BooleanField()
