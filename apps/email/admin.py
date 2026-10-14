from django import forms
from django.contrib import admin

from apps.email.link_hosts import HostRejected, host_error_text, parse_extra_hosts, record_extra_host_changes
from apps.email.models import (
    CompanyProfile,
    CompanyProvider,
    EmailRule,
    LaneState,
    LibraryTemplate,
    Message,
    ServiceClient,
)


class ServiceClientAdminForm(forms.ModelForm):
    class Meta:
        model = ServiceClient
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['allowed_company_ids'].help_text = (
            'Company ids this key may address. An empty list allows no company. '
            'null allows every company.'
        )
        if self.instance._state.adding and not self.is_bound:
            self.initial['allowed_company_ids'] = []

    def clean_allowed_company_ids(self):
        raw = self.data.get(self.add_prefix('allowed_company_ids'))
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return []
        return self.cleaned_data.get('allowed_company_ids')


@admin.register(ServiceClient)
class ServiceClientAdmin(admin.ModelAdmin):
    form = ServiceClientAdminForm
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


@admin.register(LibraryTemplate)
class LibraryTemplateAdmin(admin.ModelAdmin):
    list_display = ('key', 'set', 'name', 'company_id', 'built_in', 'updated_at')
    list_filter = ('built_in', 'set')
    search_fields = ('key', 'name')


@admin.register(EmailRule)
class EmailRuleAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'event_type', 'enabled', 'built_in', 'template')


@admin.register(LaneState)
class LaneStateAdmin(admin.ModelAdmin):
    list_display = ('lane', 'company_id', 'paused', 'reason', 'paused_at')


class CompanyProfileAdminForm(forms.ModelForm):
    class Meta:
        model = CompanyProfile
        fields = '__all__'

    def clean_auth_link_hosts(self):
        value = self.cleaned_data.get('auth_link_hosts') or []
        try:
            return parse_extra_hosts(value)
        except HostRejected as exc:
            raise forms.ValidationError(host_error_text(exc.code)) from exc


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    form = CompanyProfileAdminForm
    list_display = ('company_id', 'name', 'updated_at')
    search_fields = ('company_id', 'name')
    readonly_fields = ('application_link_hosts',)

    def save_model(self, request, obj, form, change):
        before = []
        if change and obj.pk:
            before = list(
                CompanyProfile.objects.filter(pk=obj.pk).values_list('auth_link_hosts', flat=True).first() or []
            )
        super().save_model(request, obj, form, change)
        record_extra_host_changes(
            obj.company_id,
            [str(item) for item in before if isinstance(item, str)],
            list(obj.auth_link_hosts or []),
            user_id=getattr(request.user, 'pk', None),
        )

    def delete_model(self, request, obj):
        before = [str(item) for item in (obj.auth_link_hosts or []) if isinstance(item, str)]
        company_id = obj.company_id
        super().delete_model(request, obj)
        record_extra_host_changes(company_id, before, [], user_id=getattr(request.user, 'pk', None))

    def delete_queryset(self, request, queryset):
        snapshots = list(queryset.values_list('company_id', 'auth_link_hosts'))
        super().delete_queryset(request, queryset)
        user_id = getattr(request.user, 'pk', None)
        for company_id, hosts in snapshots:
            before = [str(item) for item in (hosts or []) if isinstance(item, str)]
            record_extra_host_changes(company_id, before, [], user_id=user_id)
