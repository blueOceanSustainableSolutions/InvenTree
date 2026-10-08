"""Seed the default fault codes."""

from django.db import migrations

# (code, name, category)
DEFAULT_FAULT_CODES = [
    ('WATER_INGRESS', 'Water ingress', 'ENCLOSURE'),
    ('BIOFOULING', 'Biofouling', 'FOULING'),
    ('CORROSION', 'Corrosion', 'ENCLOSURE'),
    ('SEAL_FAILURE', 'Seal failure', 'ENCLOSURE'),
    ('BATTERY_DEGRADATION', 'Battery degradation', 'POWER'),
    ('CONNECTOR_DAMAGE', 'Connector damage', 'CONNECTOR_CABLE'),
    ('CABLE_DAMAGE', 'Cable damage', 'CONNECTOR_CABLE'),
    ('MOORING_DRAG', 'Mooring drag', 'MOORING'),
    ('MOORING_FAILURE', 'Mooring failure', 'MOORING'),
    ('FIRMWARE_FAULT', 'Firmware fault', 'FIRMWARE_COMMS'),
    ('COMMS_FAILURE', 'Communications failure', 'FIRMWARE_COMMS'),
    ('SENSOR_DRIFT', 'Sensor drift', 'SENSOR'),
    ('VANDALISM_FISHING', 'Vandalism / fishing damage', 'EXTERNAL_DAMAGE'),
    ('OTHER', 'Other', 'OTHER'),
]


def add_fault_codes(apps, schema_editor):
    """Create the default fault codes (existing codes are left unchanged)."""
    FaultCode = apps.get_model('fleet', 'FaultCode')

    for code, name, category in DEFAULT_FAULT_CODES:
        FaultCode.objects.get_or_create(
            code=code, defaults={'name': name, 'category': category}
        )


def remove_fault_codes(apps, schema_editor):
    """Remove the default fault codes."""
    FaultCode = apps.get_model('fleet', 'FaultCode')

    FaultCode.objects.filter(code__in=[c[0] for c in DEFAULT_FAULT_CODES]).delete()


class Migration(migrations.Migration):
    """Seed the default fault codes."""

    dependencies = [('fleet', '0001_initial')]

    operations = [
        migrations.RunPython(add_fault_codes, reverse_code=remove_fault_codes)
    ]
