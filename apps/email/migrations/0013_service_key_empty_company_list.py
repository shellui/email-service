from django.db import migrations


def empty_company_lists_to_null(apps, schema_editor):
    """Rows stored as [] were unrestricted. Keep that scope now that [] denies all."""
    ServiceClient = apps.get_model('email', 'ServiceClient')
    for client in ServiceClient.objects.all().iterator():
        if client.allowed_company_ids == []:
            client.allowed_company_ids = None
            client.save(update_fields=['allowed_company_ids'])


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0012_company_auth_link_hosts'),
    ]

    operations = [
        migrations.RunPython(empty_company_lists_to_null, migrations.RunPython.noop),
    ]
