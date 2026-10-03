from django.contrib import admin

from apps.email.models import CompanyEmailSettings, CompanyProvider, EmailRule, LaneState, Message, ServiceClient


@admin.register(ServiceClient)
class ServiceClientAdmin(admin.ModelAdmin):
    list_display = ('service', 'name', 'key_prefix', 'active', 'last_used_at')
    search_fields = ('service', 'key_prefix')


@admin.register(CompanyProvider)
class CompanyProviderAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'provider', 'from_email', 'configured', 'credentials_hint')
    exclude = ('credentials_ciphertext', 'webhook_ciphertext')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'company_id', 'template_key', 'lane', 'status', 'accepted_at')
    list_filter = ('lane', 'status')
    search_fields = ('to_email', 'template_key', 'provider_message_id')


@admin.register(CompanyEmailSettings)
class CompanyEmailSettingsAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'theme', 'updated_at')


@admin.register(EmailRule)
class EmailRuleAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'event_type', 'enabled', 'built_in', 'template')


@admin.register(LaneState)
class LaneStateAdmin(admin.ModelAdmin):
    list_display = ('lane', 'company_id', 'paused', 'reason', 'paused_at')
