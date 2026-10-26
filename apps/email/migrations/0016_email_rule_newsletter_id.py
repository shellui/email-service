from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0015_newsletter_subscriber_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailrule',
            name='newsletter_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
