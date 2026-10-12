from django.contrib import admin

from apps.actions.models import (
    ActionOutbox,
    ActionRule,
    EventLog,
    ScheduledJobCounter,
    ScheduledJobRun,
    ScheduledJobState,
)


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
    list_display = ('job', 'status', 'trigger', 'started_at', 'duration_ms')
    list_filter = ('job', 'status', 'trigger')


@admin.register(ScheduledJobState)
class ScheduledJobStateAdmin(admin.ModelAdmin):
    list_display = ('job', 'last_success_at', 'last_failure_at', 'last_skipped_at')


@admin.register(ScheduledJobCounter)
class ScheduledJobCounterAdmin(admin.ModelAdmin):
    list_display = ('job', 'name', 'value')
