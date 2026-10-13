"""Alert type BUILD_CANCELLED: a fleet build order was cancelled."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fleet', '0008_task_device_optional')]

    operations = [
        migrations.AlterField(
            model_name='alert',
            name='alert_type',
            field=models.CharField(
                choices=[
                    ('STREAM_LATE', 'Stream late'),
                    ('STREAM_MISSING', 'Stream missing'),
                    ('NO_CONTACT', 'No contact'),
                    ('GEOFENCE_BREACH', 'Geofence breach'),
                    ('PM_DUE', 'Maintenance due'),
                    ('PM_OVERDUE', 'Maintenance overdue'),
                    ('NOT_READY', 'Not ready'),
                    ('BUILD_CANCELLED', 'Build order cancelled'),
                    ('BUILD_LATE', 'Build late'),
                    ('NO_DEPLOY_DATE', 'No deployment date'),
                    ('PARTS_SHORT', 'Parts short'),
                ],
                max_length=20,
                verbose_name='Alert Type',
            ),
        )
    ]
