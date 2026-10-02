from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0002_lane_state_company'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_id', models.PositiveIntegerField(unique=True)),
                ('name', models.CharField(max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='EventSkip',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_id', models.PositiveIntegerField(db_index=True)),
                ('service', models.CharField(max_length=64)),
                ('event_type', models.CharField(max_length=128)),
                ('reason', models.CharField(max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['company_id', 'created_at'], name='email_skip_company_day_idx'),
                ],
            },
        ),
    ]
