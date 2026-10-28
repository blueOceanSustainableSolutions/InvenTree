"""Carry-over in a swap: consumed parts which the new device takes over.

- New CarryOverLine: a consumed stock row of the old build order picked for the
  replacement deployment (pending, moved or released)
- MaintenanceAction.action gains CARRY_OVER
"""

import InvenTree.models
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0064_buildline_carried_over'),
        ('fleet', '0011_site_location_history'),
        ('part', '0151_part_quick_build'),
        ('stock', '0126_stockitem_consumed_by_quick_build_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='maintenanceaction',
            name='action',
            field=models.CharField(choices=[('REPLACE', 'Replace'), ('REMOVE', 'Remove'), ('ADD', 'Add'), ('DESTROY', 'Destroy'), ('CONSUME', 'Consume'), ('REPAIR', 'Repair'), ('CLEAN', 'Clean'), ('FIRMWARE', 'Firmware'), ('REPOSITION', 'Move Site'), ('CARRY_OVER', 'Carry Over'), ('OTHER', 'Other')], max_length=20, verbose_name='Action'),
        ),
        migrations.CreateModel(
            name='CarryOverLine',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=5, max_digits=15, validators=[django.core.validators.MinValueValidator(0)], verbose_name='Quantity')),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('MOVED', 'Moved'), ('RELEASED', 'Released')], default='PENDING', max_length=20, verbose_name='Status')),
                ('picked_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Picked At')),
                ('closed_at', models.DateTimeField(blank=True, null=True, verbose_name='Closed At')),
                ('deployment', models.ForeignKey(help_text='The replacement deployment', on_delete=django.db.models.deletion.CASCADE, related_name='carry_over_lines', to='fleet.deployment', verbose_name='Deployment')),
                ('moved_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='stock.stockitem', verbose_name='Moved Stock Item')),
                ('part', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='part.part', verbose_name='Part')),
                ('picked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Picked By')),
                ('stock_item', models.ForeignKey(blank=True, help_text='Stock consumed by the build order of the old device', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fleet_carry_over_lines', to='stock.stockitem', verbose_name='Stock Item')),
                ('target_line', models.ForeignKey(blank=True, help_text='Line of the new build order which the stock covers', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fleet_carry_over_lines', to='build.buildline', verbose_name='Build Line')),
                ('task', models.ForeignKey(blank=True, help_text='The swap task which moved or released this line', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='carry_over_lines', to='fleet.maintenancetask', verbose_name='Task')),
            ],
            options={
                'verbose_name': 'Carry-over Line',
                'verbose_name_plural': 'Carry-over Lines',
                'ordering': ['deployment', 'pk'],
            },
            bases=(InvenTree.models.ContentTypeMixin, InvenTree.models.PluginValidationMixin, models.Model),
        ),
    ]
