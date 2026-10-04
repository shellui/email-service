from django.db import migrations, models


def tokenize_designs(apps, schema_editor):
    from apps.email.theming import tokenize

    LibraryTemplate = apps.get_model('email', 'LibraryTemplate')
    TemplateVersion = apps.get_model('email', 'TemplateVersion')
    for row in LibraryTemplate.objects.exclude(set=''):
        row.document = tokenize(row.document, row.set)
        row.save(update_fields=['document'])
    for version in TemplateVersion.objects.select_related('template').exclude(template__set=''):
        version.document = tokenize(version.document, version.template.set)
        version.translations = tokenize(version.translations, version.template.set)
        version.save(update_fields=['document', 'translations'])


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0006_template_translations'),
    ]

    operations = [
        migrations.AddField(
            model_name='librarytemplate',
            name='theme',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='templateversion',
            name='theme',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(tokenize_designs, migrations.RunPython.noop),
    ]
