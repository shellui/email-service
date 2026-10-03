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
    company_id = serializers.IntegerField()
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
