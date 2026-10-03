"""OpenAPI shapes for the email API. These serializers are documentation only."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

COMPANY_QUERY = OpenApiParameter(
    name='company_id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description='Company to read. Staff may set this. Owners use the company on the JWT when it is omitted.',
)


class ErrorSerializer(serializers.Serializer):
    error_code = serializers.CharField()
    field_errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
    )
    request_id = serializers.CharField(required=False)


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    version = serializers.CharField()


class RecipientSerializer(serializers.Serializer):
    email = serializers.EmailField()
    user_id = serializers.IntegerField(required=False)


class AcceptedMessageSerializer(serializers.Serializer):
    id = serializers.CharField()
    to = serializers.EmailField()
    status = serializers.CharField()
    lane = serializers.CharField()
    template_version = serializers.IntegerField(allow_null=True)
    language = serializers.CharField()
    expires_at = serializers.CharField(allow_null=True)


class SendRequestSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    template_key = serializers.CharField()
    language = serializers.CharField(required=False)
    lane = serializers.CharField(required=False)
    ttl_seconds = serializers.IntegerField(required=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True)
    to = RecipientSerializer(many=True)
    variables = serializers.JSONField(required=False)


class SendResponseSerializer(serializers.Serializer):
    idempotent_replay = serializers.BooleanField()
    request_id = serializers.CharField(required=False)
    messages = AcceptedMessageSerializer(many=True)


class BatchItemSerializer(serializers.Serializer):
    to = RecipientSerializer()
    variables = serializers.JSONField(required=False)


class BatchRequestSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    template_key = serializers.CharField()
    language = serializers.CharField(required=False)
    lane = serializers.CharField(required=False)
    ttl_seconds = serializers.IntegerField(required=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True)
    items = BatchItemSerializer(many=True)


class BatchAcceptedSerializer(serializers.Serializer):
    index = serializers.IntegerField()
    id = serializers.CharField()
    status = serializers.CharField()


class BatchRejectedSerializer(serializers.Serializer):
    index = serializers.IntegerField()
    error_code = serializers.CharField()


class BatchResponseSerializer(serializers.Serializer):
    idempotent_replay = serializers.BooleanField()
    request_id = serializers.CharField(required=False)
    accepted = BatchAcceptedSerializer(many=True)
    rejected = BatchRejectedSerializer(many=True)


class EventRecipientSerializer(serializers.Serializer):
    email = serializers.EmailField()
    user_id = serializers.IntegerField(required=False)


class EventRequestSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    service = serializers.CharField(required=False)
    event_type = serializers.CharField()
    idempotency_key = serializers.CharField(required=False, allow_blank=True)
    payload = serializers.JSONField(required=False)
    recipients = EventRecipientSerializer(many=True, required=False)


class EventResponseSerializer(serializers.Serializer):
    idempotent_replay = serializers.BooleanField()
    rule_enabled = serializers.BooleanField()
    skipped_reason = serializers.CharField(required=False)
    request_id = serializers.CharField(required=False)
    messages = AcceptedMessageSerializer(many=True)


class MessageEventSerializer(serializers.Serializer):
    type = serializers.CharField()
    at = serializers.CharField()


class MessageDetailSerializer(serializers.Serializer):
    id = serializers.CharField()
    company_id = serializers.IntegerField()
    service = serializers.CharField()
    template_key = serializers.CharField()
    lane = serializers.CharField()
    status = serializers.CharField()
    provider = serializers.CharField(allow_blank=True)
    accepted_at = serializers.CharField()
    expires_at = serializers.CharField(allow_null=True)
    events = MessageEventSerializer(many=True)


class MessageListItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    template_key = serializers.CharField()
    lane = serializers.CharField()
    status = serializers.CharField()
    to = serializers.CharField()
    accepted_at = serializers.CharField()


class MessageListSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    messages = MessageListItemSerializer(many=True)


class MessageCancelSerializer(serializers.Serializer):
    id = serializers.CharField()
    status = serializers.CharField()


class CatalogVariableSerializer(serializers.Serializer):
    token = serializers.CharField()
    type = serializers.CharField()
    required = serializers.BooleanField()
    description = serializers.CharField()
    example = serializers.JSONField(required=False)
    is_url = serializers.BooleanField()
    sensitive = serializers.BooleanField(required=False)
    allowed_hosts_setting = serializers.CharField(required=False)


class CatalogLanguageSerializer(serializers.Serializer):
    subject = serializers.CharField()
    preheader = serializers.CharField()


class CatalogEventSerializer(serializers.Serializer):
    service = serializers.CharField()
    event_type = serializers.CharField()
    template_key = serializers.CharField()
    label = serializers.CharField()
    lane_class = serializers.CharField()
    default_lane = serializers.CharField()
    default_enabled = serializers.BooleanField()
    category = serializers.CharField()
    default_ttl_seconds = serializers.IntegerField(allow_null=True)
    variables = CatalogVariableSerializer(many=True)
    suggested = serializers.DictField(child=CatalogLanguageSerializer())


class CatalogSerializer(serializers.Serializer):
    auth_link_hosts = serializers.ListField(child=serializers.CharField())
    events = CatalogEventSerializer(many=True)


class TemplatePackSerializer(serializers.Serializer):
    subject = serializers.CharField()
    preheader = serializers.CharField()
    document = serializers.JSONField()


class TemplateDefaultsSerializer(serializers.Serializer):
    template_key = serializers.CharField()
    languages = serializers.DictField(child=TemplatePackSerializer())
    variables = CatalogVariableSerializer(many=True)


class TemplateSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    template_key = serializers.CharField()
    language = serializers.CharField()
    company_id = serializers.IntegerField(allow_null=True)
    active_version = serializers.IntegerField(allow_null=True)


class TemplateListSerializer(serializers.Serializer):
    templates = TemplateSummarySerializer(many=True)


class TemplateCreateRequestSerializer(serializers.Serializer):
    template_key = serializers.CharField()
    language = serializers.CharField(required=False)


class TemplateCreateResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    template_key = serializers.CharField()
    language = serializers.CharField()
    draft_version = serializers.IntegerField()


class ThemePaletteSerializer(serializers.Serializer):
    background = serializers.CharField(required=False)
    foreground = serializers.CharField(required=False)
    muted = serializers.CharField(required=False)
    mutedForeground = serializers.CharField(required=False)
    primary = serializers.CharField(required=False)
    primaryForeground = serializers.CharField(required=False)
    border = serializers.CharField(required=False)


class TemplateVersionSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()
    subject = serializers.CharField()
    preheader = serializers.CharField(allow_blank=True)
    document = serializers.JSONField()
    theme_name = serializers.CharField()
    theme_palette = ThemePaletteSerializer()
    published_at = serializers.CharField(allow_null=True)


class TemplateVersionListSerializer(serializers.Serializer):
    versions = TemplateVersionSerializer(many=True)


class TemplateVersionCreateRequestSerializer(serializers.Serializer):
    subject = serializers.CharField()
    preheader = serializers.CharField(required=False, allow_blank=True)
    document = serializers.JSONField()
    theme_name = serializers.CharField(required=False)
    theme_palette = ThemePaletteSerializer(required=False)


class TemplateVersionCreateResponseSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()


class TemplatePublishResponseSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()
    checksum = serializers.CharField()


class RenderRequestSerializer(serializers.Serializer):
    template_key = serializers.CharField(required=False)
    language = serializers.CharField(required=False)
    variables = serializers.JSONField(required=False)
    document = serializers.JSONField(required=False)
    subject = serializers.CharField(required=False, allow_blank=True)
    theme_palette = ThemePaletteSerializer(required=False)


class RenderResponseSerializer(serializers.Serializer):
    subject = serializers.CharField()
    html = serializers.CharField()
    text = serializers.CharField()
    missing_variables = serializers.ListField(child=serializers.CharField())


class TemplateTestSendRequestSerializer(serializers.Serializer):
    to = serializers.EmailField(required=False)
    subject = serializers.CharField(required=False)
    preheader = serializers.CharField(required=False, allow_blank=True)
    document = serializers.JSONField(required=False)
    theme_palette = ThemePaletteSerializer(required=False)


class TestSendResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    provider = serializers.CharField()
    provider_message_id = serializers.CharField(allow_blank=True, required=False)


class EmailRuleSerializer(serializers.Serializer):
    event_type = serializers.CharField()
    service = serializers.CharField()
    template_key = serializers.CharField()
    enabled = serializers.BooleanField()
    language = serializers.CharField(allow_blank=True)
    recipient_mode = serializers.CharField()
    static_recipients = serializers.ListField(child=serializers.JSONField())
    customized = serializers.BooleanField()
    default_enabled = serializers.BooleanField()


class EmailRuleListSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    rules = EmailRuleSerializer(many=True)


class EmailRuleWriteSerializer(serializers.Serializer):
    event_type = serializers.CharField()
    enabled = serializers.BooleanField(required=False)
    template_key = serializers.CharField(required=False)
    language = serializers.CharField(required=False, allow_blank=True)
    recipient_mode = serializers.CharField(required=False)
    static_recipients = serializers.ListField(child=serializers.JSONField(), required=False)


class EmailRuleSaveResponseSerializer(serializers.Serializer):
    event_type = serializers.CharField()
    enabled = serializers.BooleanField()
    template_key = serializers.CharField()
    recipient_mode = serializers.CharField()


class ProviderCredentialsSerializer(serializers.Serializer):
    api_key = serializers.CharField(required=False)
    host = serializers.CharField(required=False)
    port = serializers.IntegerField(required=False)
    username = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(required=False)
    use_tls = serializers.BooleanField(required=False)
    use_ssl = serializers.BooleanField(required=False)


class ProviderSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    configured = serializers.BooleanField()
    provider = serializers.CharField(allow_null=True)
    from_email = serializers.CharField()
    from_name = serializers.CharField(allow_blank=True)
    sending_domain = serializers.CharField(allow_blank=True)
    bulk_from_email = serializers.CharField(allow_blank=True)
    credentials_hint = serializers.CharField(allow_blank=True)
    webhook_configured = serializers.BooleanField()
    webhook_hint = serializers.CharField(allow_blank=True)
    fallback_provider = serializers.CharField()
    fallback_configured = serializers.BooleanField()
    smtp_allowed = serializers.BooleanField()
    auth_link_hosts = serializers.ListField(child=serializers.CharField())


class ProviderWriteSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=['resend', 'smtp'])
    from_email = serializers.EmailField()
    from_name = serializers.CharField(required=False, allow_blank=True)
    sending_domain = serializers.CharField(required=False, allow_blank=True)
    bulk_from_email = serializers.CharField(required=False, allow_blank=True)
    credentials = ProviderCredentialsSerializer(required=False)
    webhook_secret = serializers.CharField(required=False, allow_blank=True)


class ProviderTestSendRequestSerializer(serializers.Serializer):
    to = serializers.EmailField(required=False)


class CountSerializer(serializers.Serializer):
    sent = serializers.IntegerField()
    delivered = serializers.IntegerField()
    bounced = serializers.IntegerField()
    complained = serializers.IntegerField()
    expired = serializers.IntegerField()
    failed = serializers.IntegerField()
    queued = serializers.IntegerField()
    suppressed = serializers.IntegerField()
    cancelled = serializers.IntegerField()


class SkippedSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    no_recipients = serializers.IntegerField()
    rule_disabled = serializers.IntegerField()


class StatsDaySerializer(CountSerializer):
    day = serializers.CharField()


class StatsSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    to = serializers.CharField()
    totals = CountSerializer()
    skipped = SkippedSerializer()
    by_lane = serializers.DictField(child=CountSerializer())
    by_event = serializers.DictField(child=CountSerializer())
    by_day = StatsDaySerializer(many=True)


StatsSerializer._declared_fields['from'] = serializers.CharField()


class SuppressionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email_masked = serializers.CharField()
    reason = serializers.CharField()
    lanes = serializers.ListField(child=serializers.CharField())
    expires_at = serializers.CharField(allow_null=True)


class SuppressionListSerializer(serializers.Serializer):
    suppressions = SuppressionSerializer(many=True)


class SuppressionCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    reason = serializers.CharField(required=False)
    lanes = serializers.ListField(child=serializers.CharField(), required=False)


class SuppressionCreatedSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email_masked = serializers.CharField()
    reason = serializers.CharField()


class LaneStateSerializer(serializers.Serializer):
    lane = serializers.CharField()
    paused = serializers.BooleanField()


class PrivacyEraseRequestSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    email = serializers.EmailField()


class PrivacyEraseResponseSerializer(serializers.Serializer):
    deleted_messages = serializers.IntegerField()
    email_masked = serializers.CharField()


class WebhookAckSerializer(serializers.Serializer):
    status = serializers.CharField()


class WebhookEventSerializer(serializers.Serializer):
    type = serializers.CharField(required=False)
    data = serializers.JSONField(required=False)


class ServiceClientSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    service = serializers.CharField()
    name = serializers.CharField()
    key_prefix = serializers.CharField()
    allowed_lanes = serializers.ListField(child=serializers.CharField())
    allowed_template_prefixes = serializers.ListField(child=serializers.CharField())
    active = serializers.BooleanField()
    last_used_at = serializers.CharField(allow_null=True)


class ServiceClientListSerializer(serializers.Serializer):
    clients = ServiceClientSerializer(many=True)


class ServiceClientCreateSerializer(serializers.Serializer):
    service = serializers.CharField()
    name = serializers.CharField(required=False)
    allowed_lanes = serializers.ListField(child=serializers.CharField(), required=False)
    allowed_template_prefixes = serializers.ListField(child=serializers.CharField(), required=False)
    allowed_company_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class ServiceClientCreatedSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    service = serializers.CharField()
    key = serializers.CharField()
    key_prefix = serializers.CharField()
