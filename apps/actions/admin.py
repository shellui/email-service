from django.contrib import admin

from apps.actions.models import ActionOutbox, ActionRule, EventLog, ScheduledJobRun


@admin.register(ActionRule)
class ActionRuleAdmin(admin.ModelAdmin):
    list_display = ('company_id', 'name', 'event_type', 'enabled')


@admin.register(ActionOutbox)
class ActionOutboxAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'company_id', 'status', 'attempt_count', 'created_at')


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'company_id', 'created_at')


@admin.register(ScheduledJobRun)
class ScheduledJobRunAdmin(admin.ModelAdmin):
    list_display = ('job', 'status', 'trigger', 'started_at', 'duration_ms', 'error_key')
    list_filter = ('job', 'status', 'trigger')
    readonly_fields = [field.name for field in ScheduledJobRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
