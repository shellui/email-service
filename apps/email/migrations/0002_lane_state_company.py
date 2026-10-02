from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='lanestate',
            name='company_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='lanestate',
            name='lane',
            field=models.CharField(max_length=32),
        ),
        migrations.AddConstraint(
            model_name='lanestate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('company_id__isnull', True)),
                fields=('lane',),
                name='uniq_global_lane_state',
            ),
        ),
        migrations.AddConstraint(
            model_name='lanestate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('company_id__isnull', False)),
                fields=('lane', 'company_id'),
                name='uniq_company_lane_state',
            ),
        ),
    ]
