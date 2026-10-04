from django.db import migrations, models


def _clear_block_templates(apps, schema_editor):
    """Block documents do not convert to editor documents. Rules and copies start over."""
    apps.get_model('email', 'EmailRule').objects.all().delete()
    apps.get_model('email', 'TemplateVersion').objects.all().delete()
    apps.get_model('email', 'EmailTemplate').objects.all().delete()


def _noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0004_themes_and_rules'),
    ]

    operations = [
        migrations.RunPython(_clear_block_templates, _noop),
        migrations.RemoveConstraint(
            model_name='emailtemplate',
            name='uniq_template_company_lang',
        ),
        migrations.RemoveConstraint(
            model_name='emailtemplate',
            name='uniq_platform_template_lang',
        ),
        migrations.AlterField(
            model_name='emailtemplate',
            name='template_key',
            field=models.CharField(max_length=128, unique=True),
        ),
        migrations.AlterField(
            model_name='emailtemplate',
            name='company_id',
            field=models.PositiveIntegerField(db_index=True),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='source_key',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='set',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RemoveField(
            model_name='templateversion',
            name='theme_name',
        ),
        migrations.RemoveField(
            model_name='templateversion',
            name='theme_palette',
        ),
        migrations.DeleteModel(
            name='CompanyEmailSettings',
        ),
        migrations.CreateModel(
            name='LibraryTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=128, unique=True)),
                ('set', models.CharField(blank=True, max_length=32)),
                ('name', models.CharField(max_length=120)),
                ('company_id', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('built_in', models.BooleanField(default=False)),
                ('subject', models.CharField(blank=True, max_length=255)),
                ('preheader', models.CharField(blank=True, max_length=255)),
                ('document', models.JSONField(default=dict)),
                ('html', models.TextField(blank=True)),
                ('text', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-built_in', 'set', 'name', 'id'],
            },
        ),
    ]
