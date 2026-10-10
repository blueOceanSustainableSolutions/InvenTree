# Align the fleet models with the InvenTree conventions:
# - health, stream state and alert severity become status codes (integers)
# - FieldTrip.responsible becomes an Owner (a user or a group)
# - DeviceLink.notes is renamed to comment (notes is the markdown field)
# - Deployment, FieldTrip and MaintenanceTask get barcode fields

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

HEALTH = {'UNKNOWN': 10, 'OK': 20, 'DEGRADED': 30, 'CRITICAL': 40}
STREAM_STATE = {'UNKNOWN': 10, 'OK': 20, 'LATE': 30, 'MISSING': 40}
SEVERITY = {'INFO': 10, 'WARNING': 20, 'CRITICAL': 30}

# (model, field, mapping, old default)
CODE_FIELDS = [
    ('deployment', 'health', HEALTH, 'UNKNOWN'),
    ('datastream', 'state', STREAM_STATE, 'UNKNOWN'),
    ('alert', 'severity', SEVERITY, 'WARNING'),
]


def codes_forward(apps, schema_editor):
    """Copy each text value into the new integer field."""
    for model_name, field, mapping, default in CODE_FIELDS:
        model = apps.get_model('fleet', model_name)

        for text, code in mapping.items():
            model.objects.filter(**{field: text}).update(**{f'{field}_code': code})


def codes_reverse(apps, schema_editor):
    """Copy each integer code back into the text field."""
    for model_name, field, mapping, default in CODE_FIELDS:
        model = apps.get_model('fleet', model_name)

        for text, code in mapping.items():
            model.objects.filter(**{f'{field}_code': code}).update(**{field: text})


def responsible_forward(apps, schema_editor):
    """Point each trip at the Owner of its responsible user."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Owner = apps.get_model('users', 'Owner')
    FieldTrip = apps.get_model('fleet', 'FieldTrip')

    trips = FieldTrip.objects.filter(responsible__isnull=False)

    if not trips.exists():
        return

    user_type, _created = ContentType.objects.get_or_create(
        app_label='auth', model='user'
    )

    for trip in trips:
        owner, _created = Owner.objects.get_or_create(
            owner_type=user_type, owner_id=trip.responsible_id
        )
        trip.responsible_owner = owner
        trip.save(update_fields=['responsible_owner'])


def responsible_reverse(apps, schema_editor):
    """Point each trip back at its responsible user (a group is dropped)."""
    FieldTrip = apps.get_model('fleet', 'FieldTrip')

    for trip in FieldTrip.objects.filter(responsible_owner__isnull=False).select_related(
        'responsible_owner__owner_type'
    ):
        owner = trip.responsible_owner

        if owner.owner_type.app_label == 'auth' and owner.owner_type.model == 'user':
            trip.responsible_id = owner.owner_id
            trip.save(update_fields=['responsible'])


def barcode_fields(model_name):
    """The InvenTreeBarcodeMixin fields for a model."""
    return [
        migrations.AddField(
            model_name=model_name,
            name='barcode_data',
            field=models.CharField(
                blank=True,
                help_text='Third party barcode data',
                max_length=500,
                verbose_name='Barcode Data',
            ),
        ),
        migrations.AddField(
            model_name=model_name,
            name='barcode_hash',
            field=models.CharField(
                blank=True,
                help_text='Unique hash of barcode data',
                max_length=128,
                verbose_name='Barcode Hash',
            ),
        ),
    ]


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('fleet', '0006_p7_device_state'),
        ('users', '0015_alter_userprofile_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Status codes: add an integer field, copy, swap
        migrations.AddField(
            model_name='deployment',
            name='health_code',
            field=models.PositiveIntegerField(default=10),
        ),
        migrations.AddField(
            model_name='datastream',
            name='state_code',
            field=models.PositiveIntegerField(default=10),
        ),
        migrations.AddField(
            model_name='alert',
            name='severity_code',
            field=models.PositiveIntegerField(default=20),
        ),
        migrations.RunPython(codes_forward, reverse_code=codes_reverse),
        migrations.RemoveField(model_name='deployment', name='health'),
        migrations.RemoveField(model_name='datastream', name='state'),
        migrations.RemoveField(model_name='alert', name='severity'),
        migrations.RenameField(
            model_name='deployment', old_name='health_code', new_name='health'
        ),
        migrations.RenameField(
            model_name='datastream', old_name='state_code', new_name='state'
        ),
        migrations.RenameField(
            model_name='alert', old_name='severity_code', new_name='severity'
        ),
        migrations.AlterField(
            model_name='deployment',
            name='health',
            field=models.PositiveIntegerField(
                choices=[
                    (10, 'Unknown'),
                    (20, 'OK'),
                    (30, 'Degraded'),
                    (40, 'Critical'),
                ],
                default=10,
                verbose_name='Health',
            ),
        ),
        migrations.AlterField(
            model_name='datastream',
            name='state',
            field=models.PositiveIntegerField(
                choices=[(10, 'Unknown'), (20, 'OK'), (30, 'Late'), (40, 'Missing')],
                default=10,
                verbose_name='State',
            ),
        ),
        migrations.AlterField(
            model_name='alert',
            name='severity',
            field=models.PositiveIntegerField(
                choices=[(10, 'Info'), (20, 'Warning'), (30, 'Critical')],
                default=20,
                verbose_name='Severity',
            ),
        ),
        # Responsible: user -> owner
        migrations.AddField(
            model_name='fieldtrip',
            name='responsible_owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='users.owner',
            ),
        ),
        migrations.RunPython(responsible_forward, reverse_code=responsible_reverse),
        migrations.RemoveField(model_name='fieldtrip', name='responsible'),
        migrations.RenameField(
            model_name='fieldtrip', old_name='responsible_owner', new_name='responsible'
        ),
        migrations.AlterField(
            model_name='fieldtrip',
            name='responsible',
            field=models.ForeignKey(
                blank=True,
                help_text='User or group responsible for this trip',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fleet_trips_responsible',
                to='users.owner',
                verbose_name='Responsible',
            ),
        ),
        # DeviceLink: notes -> comment
        migrations.RenameField(
            model_name='devicelink', old_name='notes', new_name='comment'
        ),
        migrations.AlterField(
            model_name='devicelink',
            name='comment',
            field=models.CharField(blank=True, max_length=250, verbose_name='Comment'),
        ),
        # Barcodes
        *barcode_fields('deployment'),
        *barcode_fields('fieldtrip'),
        *barcode_fields('maintenancetask'),
    ]
