"""Celery task for the email queue sweep. See docs/scheduled-jobs.md."""

from celery import shared_task

from config.scheduled_tasks import run_locked_command

# The sweep also moves broadcasts forward, which can call the provider several times.
SWEEP_EMAIL_QUEUE_LOCK_TTL = 300


@shared_task(name='email.sweep_email_queue', ignore_result=True)
def sweep_email_queue() -> str:
    """Expire overdue messages, release stale send leases, and move broadcasts forward."""
    return run_locked_command('sweep_email_queue', ttl=SWEEP_EMAIL_QUEUE_LOCK_TTL)
