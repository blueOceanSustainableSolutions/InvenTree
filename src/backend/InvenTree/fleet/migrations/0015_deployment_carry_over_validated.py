"""The carry-over of a replacement is validated before it goes on a trip.

The replacements which already have a swap task on a trip are marked as
validated, so that the plan list does not ask for a validation which their
planning did not need.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def validate_scheduled(apps, schema_editor):
    """Mark the replacements already on a trip as validated."""
    Deployment = apps.get_model('fleet', 'Deployment')

    Deployment.objects.filter(
        deployment_type='REPLACEMENT',
        tasks__task_type='SWAP',
        tasks__trip__isnull=False,
    ).update(carry_over_validated_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0014_deployment_manually_planned'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='carry_over_validated_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When the carry-over was validated: a replacement goes on a trip only once its carry-over is validated',
                verbose_name='Carry-over Validated At',
            ),
        ),
        migrations.AddField(
            model_name='deployment',
            name='carry_over_validated_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Carry-over Validated By',
            ),
        ),
        migrations.RunPython(validate_scheduled, migrations.RunPython.noop),
    ]
