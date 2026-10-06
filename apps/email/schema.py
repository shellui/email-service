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
    missing_variables = serializers.ListField(child=serializers.CharField(), required=False)
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
    link_token = serializers.CharField(allow_null=True)
    default_template = serializers.CharField()
    rules_allowed = serializers.BooleanField()
    suggested = serializers.DictField(child=CatalogLanguageSerializer())


class CatalogSerializer(serializers.Serializer):
    auth_link_hosts = serializers.ListField(child=serializers.CharField())
    events = CatalogEventSerializer(many=True)


class LibrarySetSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()


class LibrarySummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    key = serializers.CharField()
    set = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    built_in = serializers.BooleanField()
    company_id = serializers.IntegerField(allow_null=True)
    subject = serializers.CharField(allow_blank=True)
    preheader = serializers.CharField(allow_blank=True)
    updated_at = serializers.CharField(allow_null=True)
    html = serializers.CharField(allow_blank=True)


class LibraryListSerializer(serializers.Serializer):
    sets = LibrarySetSerializer(many=True)
    templates = LibrarySummarySerializer(many=True)


class LibraryVariableSerializer(serializers.Serializer):
    token = serializers.CharField()
    type = serializers.CharField()
    required = serializers.BooleanField()
    example = serializers.CharField()
    is_url = serializers.BooleanField()


THEME_HELP = (
    'Colors that repaint a library design: {name, label, colors: {role: "#rrggbb"}}. '
    'Roles: background, foreground, card, muted, muted_foreground, primary, primary_foreground, border. '
    '{} keeps the design\'s own colors.'
)


class LibraryDetailSerializer(LibrarySummarySerializer):
    document = serializers.JSONField()
    theme = serializers.JSONField(help_text=THEME_HELP + ' Copies made from this template start with it.')
    text = serializers.CharField(allow_blank=True)
    head = serializers.CharField(allow_blank=True)
    variables = LibraryVariableSerializer(many=True)


class LibraryWriteSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    source_id = serializers.IntegerField(required=False, help_text='Library template to duplicate. Create only.')
    subject = serializers.CharField(required=False, allow_blank=True)
    preheader = serializers.CharField(required=False, allow_blank=True)
    document = serializers.JSONField(required=False)
    theme = serializers.JSONField(required=False, help_text=THEME_HELP + ' A duplicate keeps its source\'s when omitted.')


class TemplateSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    template_key = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    event_type = serializers.CharField(allow_blank=True)
    kind = serializers.ChoiceField(choices=['event', 'broadcast'], help_text='broadcast: the content of a broadcast.')
    language = serializers.CharField()
    company_id = serializers.IntegerField()
    active_version = serializers.IntegerField(allow_null=True)
    source_key = serializers.CharField(allow_blank=True)
    set = serializers.CharField(allow_blank=True)
    head = serializers.CharField(allow_blank=True)


class TemplateListSerializer(serializers.Serializer):
    templates = TemplateSummarySerializer(many=True)


class TemplateVersionSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()
    subject = serializers.CharField()
    preheader = serializers.CharField(allow_blank=True)
    document = serializers.JSONField()
    translations = serializers.JSONField(
        help_text='Other languages: {lang: {subject, preheader, blocks: {textId: {content, source}}}}. Same layout as document.',
    )
    theme = serializers.JSONField(help_text=THEME_HELP)
    published_at = serializers.CharField(allow_null=True)


class TemplateVersionListSerializer(serializers.Serializer):
    versions = TemplateVersionSerializer(many=True)


class TemplateVersionCreateRequestSerializer(serializers.Serializer):
    subject = serializers.CharField(required=False)
    preheader = serializers.CharField(required=False, allow_blank=True)
    document = serializers.JSONField(required=False)
    translations = serializers.JSONField(
        required=False,
        help_text='Other languages of the copy. Kept from the latest version when omitted; a start over keeps their subjects only.',
    )
    theme = serializers.JSONField(
        required=False,
        help_text=THEME_HELP + ' Kept from the latest version when omitted. A start over takes the library template\'s, if it has one.',
    )
    library_id = serializers.IntegerField(
        required=False,
        help_text='Start over from this library template. Subject and preheader stay unless sent.',
    )


class TemplateVersionCreateResponseSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()


class TemplatePublishResponseSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    state = serializers.CharField()
    checksum = serializers.CharField()


class TemplateTestSendRequestSerializer(serializers.Serializer):
    to = serializers.EmailField(required=False)
    subject = serializers.CharField(required=False)
    preheader = serializers.CharField(required=False, allow_blank=True)
    document = serializers.JSONField(required=False)
    theme = serializers.JSONField(required=False, help_text=THEME_HELP + ' Used with document.')


class TestSendResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    provider = serializers.CharField()
    provider_message_id = serializers.CharField(allow_blank=True, required=False)


class EmailRuleContentSerializer(serializers.Serializer):
    library_id = serializers.IntegerField(help_text='Library template the event copy starts from.')
    theme = serializers.JSONField(
        required=False,
        help_text=THEME_HELP + ' Used when the library template has none.',
    )


class EmailRuleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    service = serializers.CharField()
    event_type = serializers.CharField()
    enabled = serializers.BooleanField()
    recipient_mode = serializers.CharField()
    static_recipients = serializers.ListField(child=serializers.CharField())
    language = serializers.CharField(allow_blank=True)
    template_id = serializers.IntegerField()
    built_in = serializers.BooleanField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()


class EmailRuleListSerializer(serializers.Serializer):
    company_id = serializers.IntegerField()
    rules = EmailRuleSerializer(many=True)


class EmailRuleWriteSerializer(serializers.Serializer):
    service = serializers.CharField(required=False)
    event_type = serializers.CharField()
    enabled = serializers.BooleanField(required=False)
    recipient_mode = serializers.CharField(required=False)
    static_recipients = serializers.ListField(child=serializers.CharField(), required=False)
    language = serializers.CharField(required=False, allow_blank=True)
    content = EmailRuleContentSerializer()


class EmailRulePatchSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    recipient_mode = serializers.CharField(required=False)
    static_recipients = serializers.ListField(child=serializers.CharField(), required=False)
    language = serializers.CharField(required=False, allow_blank=True)


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
    no_rule = serializers.IntegerField()


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


class BroadcastAudienceSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=['filter', 'pick', 'newsletter'],
        help_text='filter: members matching every filter. pick: chosen users and addresses. newsletter: confirmed subscribers of list_id.',
    )
    list_id = serializers.IntegerField(required=False, help_text='Newsletter list, for mode newsletter.')
    group_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    roles = serializers.ListField(child=serializers.ChoiceField(choices=['owner', 'staff', 'member']), required=False)
    access = serializers.ChoiceField(choices=['enabled', 'disabled', 'any'], required=False)
    joined_after = serializers.CharField(required=False, allow_blank=True, help_text='ISO date.')
    joined_before = serializers.CharField(required=False, allow_blank=True, help_text='ISO date, inclusive.')
    seen_after = serializers.CharField(required=False, allow_blank=True, help_text='ISO date.')
    seen_before = serializers.CharField(required=False, allow_blank=True, help_text='ISO date, inclusive. Never-seen users match.')
    user_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    emails = serializers.ListField(child=serializers.CharField(), required=False, help_text='Addresses outside the directory. They get the main language.')


class BroadcastCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    skipped_unsubscribed = serializers.IntegerField()
    skipped_suppressed = serializers.IntegerField()
    queued = serializers.IntegerField()
    sent = serializers.IntegerField()
    delivered = serializers.IntegerField()
    bounced = serializers.IntegerField()
    complained = serializers.IntegerField()
    failed = serializers.IntegerField()


class BroadcastSenderSerializer(serializers.Serializer):
    from_email = serializers.CharField(allow_blank=True)
    from_name = serializers.CharField(allow_blank=True)
    delivery = serializers.CharField(allow_blank=True, help_text='resend or bulk_lane. Empty when there is no bulk sender.')
    error_code = serializers.CharField(allow_blank=True)


class BroadcastSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    state = serializers.ChoiceField(choices=['draft', 'queued', 'preparing', 'sending', 'sent', 'failed'])
    delivery = serializers.CharField(allow_blank=True)
    from_email = serializers.CharField(allow_blank=True)
    template_id = serializers.IntegerField()
    template_version = serializers.IntegerField(allow_null=True)
    language = serializers.CharField()
    audience = BroadcastAudienceSerializer()
    counts = BroadcastCountsSerializer(allow_null=True)
    last_error_code = serializers.CharField(allow_blank=True)
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    sent_at = serializers.CharField(allow_null=True)
    sender = BroadcastSenderSerializer(required=False)


class BroadcastListSerializer(serializers.Serializer):
    broadcasts = BroadcastSerializer(many=True)


class BroadcastCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    source_key = serializers.CharField(help_text='Library template to start from.')
    language = serializers.CharField(required=False, help_text='Main language of the content. Default en.')
    audience = BroadcastAudienceSerializer(required=False)


class BroadcastPatchSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    audience = BroadcastAudienceSerializer(required=False)


class BroadcastPreviewRequestSerializer(serializers.Serializer):
    audience = BroadcastAudienceSerializer(required=False, help_text='Defaults to the saved audience.')


class BroadcastPreviewSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    sendable = serializers.IntegerField()
    unsubscribed = serializers.IntegerField()
    suppressed = serializers.IntegerField()
    languages = serializers.DictField(child=serializers.IntegerField(), help_text='Recipients per send language.')
    samples = serializers.ListField(child=serializers.CharField(), help_text='A few masked addresses.')


class NewsletterCountsSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    confirmed = serializers.IntegerField()
    unsubscribed = serializers.IntegerField()


class NewsletterSenderSerializer(serializers.Serializer):
    from_email = serializers.CharField(allow_blank=True, help_text='From address of the confirmation email.')
    error_code = serializers.CharField(allow_blank=True, help_text='Why confirmation emails cannot be sent. Empty when they can.')


class NewsletterSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    public_key = serializers.CharField(help_text='Public key used in the sign-up URL. Not a secret.')
    default_language = serializers.CharField()
    allowed_origins = serializers.ListField(
        child=serializers.CharField(), help_text='Websites allowed to post the form. Empty means any.'
    )
    confirmed_redirect_url = serializers.CharField(allow_blank=True)
    turnstile_site_key = serializers.CharField(allow_blank=True)
    turnstile_configured = serializers.BooleanField()
    confirmation_template_id = serializers.IntegerField()
    subscribe_url = serializers.CharField()
    counts = NewsletterCountsSerializer()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    sender = NewsletterSenderSerializer(required=False)


class NewsletterListSerializer(serializers.Serializer):
    newsletters = NewsletterSerializer(many=True)


class NewsletterWriteSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    default_language = serializers.CharField(required=False)
    allowed_origins = serializers.ListField(child=serializers.CharField(), required=False)
    confirmed_redirect_url = serializers.CharField(required=False, allow_blank=True)
    turnstile_site_key = serializers.CharField(required=False, allow_blank=True)
    turnstile_secret = serializers.CharField(required=False, allow_blank=True, help_text='Write only. Empty clears it.')


class NewsletterSubscriberSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.CharField(help_text='Masked once the address is cleared after unsubscribing.')
    first_name = serializers.CharField(allow_blank=True)
    language = serializers.CharField()
    status = serializers.ChoiceField(choices=['pending', 'confirmed', 'unsubscribed'])
    source = serializers.ChoiceField(choices=['form', 'admin', 'import'])
    created_at = serializers.CharField()
    confirmed_at = serializers.CharField(allow_null=True)
    unsubscribed_at = serializers.CharField(allow_null=True)


class NewsletterSubscriberPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = NewsletterSubscriberSerializer(many=True)


class NewsletterSubscriberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    language = serializers.CharField(required=False, allow_blank=True)
    mode = serializers.ChoiceField(
        choices=['confirm', 'consented'],
        required=False,
        help_text='confirm (default) sends the confirmation email. consented adds the address as confirmed.',
    )


class NewsletterSubscriberAddedSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=['sent', 'existing', 'throttled', 'suppressed', 'added'])
    subscriber = NewsletterSubscriberSerializer()


class NewsletterImportRequestSerializer(serializers.Serializer):
    csv = serializers.CharField(help_text='CSV text with an email column, and optional first_name and language.')
    mode = serializers.ChoiceField(choices=['confirm', 'consented'], required=False)


class NewsletterImportSerializer(serializers.Serializer):
    added = serializers.IntegerField()
    existing = serializers.IntegerField()
    invalid = serializers.IntegerField()
    skipped_unsubscribed = serializers.IntegerField()
    skipped_suppressed = serializers.IntegerField()


class NewsletterSubscribeRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    language = serializers.CharField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    website = serializers.CharField(required=False, allow_blank=True, help_text='Honeypot. Leave empty.')
    turnstile_token = serializers.CharField(required=False, allow_blank=True)


class NewsletterSubscribeResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['pending'])
