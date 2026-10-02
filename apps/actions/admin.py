from django.contrib import admin

from apps.actions.models import ActionOutbox, ActionRule, EventLog


@admin.register(ActionRule)
class ActionRuleAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'name', 'event_type', 'enabled')


@admin.register(ActionOutbox)
class ActionOutboxAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'company_id', 'status', 'attempt_count', 'created_at')


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'company_id', 'created_at')
