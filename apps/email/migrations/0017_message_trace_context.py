from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0016_email_rule_newsletter_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='traceparent',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='message',
            name='tracestate',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
    ]
