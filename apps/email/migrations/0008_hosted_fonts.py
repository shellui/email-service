from django.db import migrations


def recompose_designs(apps, schema_editor):
    """Library heads load fonts from email-service now. Empty HTML composes again on next use."""
    LibraryTemplate = apps.get_model('email', 'LibraryTemplate')
    TemplateVersion = apps.get_model('email', 'TemplateVersion')
    LibraryTemplate.objects.exclude(set='').update(html='', text='')
    TemplateVersion.objects.exclude(template__set='').update(html='', text='', rendered={})


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0007_email_themes'),
    ]

    operations = [
        migrations.RunPython(recompose_designs, migrations.RunPython.noop),
    ]
