from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0011_auth_rules_locked'),
    ]

    operations = [
        migrations.AddField(
            model_name='companyprofile',
            name='application_link_hosts',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Hostnames of this company's identity application URLs. Synced from identity. Not edited here."
                ),
                verbose_name='Application link hosts',
            ),
        ),
        migrations.AddField(
            model_name='companyprofile',
            name='auth_link_hosts',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Extra hostnames this company may use in auth mail, such as playground.shellui.com. '
                    'Exact hostnames only: no scheme, path, port, wildcard, or IP address. '
                    'localhost is refused when DEBUG is off. '
                    'Identity application domains and EMAIL_AUTH_LINK_HOSTS are included in addition to this list.'
                ),
                verbose_name='Allowed link hosts',
            ),
        ),
    ]
