"""Celery tasks for the Shellui Actions scheduled jobs. See docs/scheduled-jobs.md."""

from celery import shared_task

from config.scheduled_tasks import run_locked_command

# A run sends up to 50 deliveries with a 5 second timeout each, so the lock covers the
# slowest batch. If a worker dies, retries resume after at most 5 minutes.
RETRY_WEBHOOKS_LOCK_TTL = 300
PURGE_EXPIRED_DATA_LOCK_TTL = 900


@shared_task(name='actions.retry_webhooks', ignore_result=True)
def retry_webhooks() -> str:
    """Retry pending Shellui Actions webhook deliveries."""
    return run_locked_command('retry_webhooks', ttl=RETRY_WEBHOOKS_LOCK_TTL)


@shared_task(name='actions.purge_expired_data', ignore_result=True)
def purge_expired_data() -> str:
    """Delete expired messages, event log rows, deliveries, and newsletter sign-ups."""
    return run_locked_command('purge_expired_data', ttl=PURGE_EXPIRED_DATA_LOCK_TTL)
