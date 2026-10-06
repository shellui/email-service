"""Auth-lane events send only through their built-in rule, to the event's recipient.

Built-in rules go back to ``hints`` with no static address. Company rules written on
an auth-lane event before the API refused them are disabled. They can still be deleted.
"""

from django.db import migrations

AUTH_EVENTS = ('identity.auth.magic_link.requested', 'identity.user.invited')


def lock_auth_rules(apps, schema_editor):
    EmailRule = apps.get_model('email', 'EmailRule')
    EmailRule.objects.filter(event_type__in=AUTH_EVENTS, built_in=True).update(
        recipient_mode='hints', static_recipients=[]
    )
    EmailRule.objects.filter(event_type__in=AUTH_EVENTS, built_in=False).update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [
        ('email', '0010_newsletters'),
    ]

    operations = [
        migrations.RunPython(lock_auth_rules, migrations.RunPython.noop),
    ]
