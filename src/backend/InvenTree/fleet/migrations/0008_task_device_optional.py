"""A deployment task can be on a trip before its unit is built."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('fleet', '0007_inventree_conventions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='maintenancetask',
            name='device',
            field=models.ForeignKey(
                blank=True,
                help_text='Serialized device worked on (empty while the unit of a deployment task is not built yet)',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='fleet_tasks',
                to='stock.stockitem',
                verbose_name='Device',
            ),
        )
    ]
