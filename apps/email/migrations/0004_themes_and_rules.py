import secrets

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def _copy_enabled_rules(apps, schema_editor):
    EmailRule = apps.get_model('email', 'EmailRule')
    EmailTemplate = apps.get_model('email', 'EmailTemplate')
    TemplateVersion = apps.get_model('email', 'TemplateVersion')
    TemplateVersion.objects.filter(theme_name='shellui').update(theme_name='barebone')

    from apps.email.catalog import get_definition

    for rule in list(EmailRule.objects.all()):
        if rule.company_id is None or not rule.enabled:
            rule.delete()
            continue
        definition = get_definition(rule.event_type) or get_definition(rule.template_key)
        language = rule.language or 'en'
        source = EmailTemplate.objects.filter(
            template_key=rule.template_key,
            company_id=rule.company_id,
            language=language,
            active_version__isnull=False,
        ).first()
        version = None
        if source is not None and source.active_version:
            version = TemplateVersion.objects.filter(
                template_id=source.pk,
                number=source.active_version,
                state='published',
            ).first()
        if version is None and definition is not None:
            pack = definition['languages'].get(language) or definition['languages'].get('en')
        else:
            pack = None
        if version is None and pack is None:
            rule.delete()
            continue
        if version is not None:
            subject = version.subject
            preheader = version.preheader
            document = version.document
            theme_name = version.theme_name or 'barebone'
            palette = version.theme_palette or {}
        else:
            subject = pack['subject']
            preheader = pack.get('preheader') or ''
            document = pack['document']
            theme_name = 'barebone'
            palette = {}
        label = definition['label'] if definition else rule.event_type
        template = EmailTemplate.objects.create(
            template_key='company.' + secrets.token_hex(6),
            company_id=rule.company_id,
            language=language,
            name=label[:120],
            event_type=rule.event_type,
            active_version=1,
        )
        TemplateVersion.objects.create(
            template=template,
            number=1,
            state='published',
            subject=subject,
            preheader=preheader,
            document=document,
            html='',
            text='',
            theme_name='barebone' if theme_name == 'shellui' else theme_name,
            theme_palette=palette,
            published_at=django.utils.timezone.now(),
        )
        rule.template_id = template.pk
        rule.built_in = False
        rule.save(update_fields=['template', 'built_in'])
    EmailRule.objects.filter(template__isnull=True).delete()


def _noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0003_company_profile_event_skip'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailtemplate',
            name='event_type',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='name',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AlterField(
            model_name='templateversion',
            name='theme_name',
            field=models.CharField(default='barebone', max_length=64),
        ),
        migrations.CreateModel(
            name='CompanyEmailSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_id', models.PositiveIntegerField(unique=True)),
                ('theme', models.CharField(default='barebone', max_length=32)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name='emailrule',
            name='built_in',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='emailrule',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='emailrule',
            name='template',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='rules',
                to='email.emailtemplate',
            ),
        ),
        migrations.RunPython(_copy_enabled_rules, _noop),
        migrations.RemoveConstraint(
            model_name='emailrule',
            name='uniq_company_email_rule',
        ),
        migrations.RemoveConstraint(
            model_name='emailrule',
            name='uniq_platform_email_rule',
        ),
        migrations.RemoveField(
            model_name='emailrule',
            name='template_key',
        ),
        migrations.AlterField(
            model_name='emailrule',
            name='company_id',
            field=models.PositiveIntegerField(db_index=True),
        ),
        migrations.AlterField(
            model_name='emailrule',
            name='enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='emailrule',
            name='template',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='rules',
                to='email.emailtemplate',
            ),
        ),
        migrations.AddConstraint(
            model_name='emailrule',
            constraint=models.UniqueConstraint(
                condition=models.Q(('built_in', True)),
                fields=('company_id', 'event_type'),
                name='uniq_builtin_email_rule',
            ),
        ),
        migrations.AddIndex(
            model_name='emailrule',
            index=models.Index(fields=['company_id', 'service', 'event_type'], name='email_rule_lookup_idx'),
        ),
    ]
