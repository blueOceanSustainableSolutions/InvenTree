"""Store coordinates with 10 decimal places, so any DD, DDM or DMS input converts back exactly."""

import django.core.validators
from django.db import migrations, models


def latitude(verbose_name, nullable=True):
    """A latitude field (signed decimal degrees)."""
    return models.DecimalField(
        blank=nullable,
        null=nullable,
        decimal_places=10,
        max_digits=12,
        validators=[
            django.core.validators.MinValueValidator(-90),
            django.core.validators.MaxValueValidator(90),
        ],
        verbose_name=verbose_name,
    )


def longitude(verbose_name, nullable=True):
    """A longitude field (signed decimal degrees)."""
    return models.DecimalField(
        blank=nullable,
        null=nullable,
        decimal_places=10,
        max_digits=13,
        validators=[
            django.core.validators.MinValueValidator(-180),
            django.core.validators.MaxValueValidator(180),
        ],
        verbose_name=verbose_name,
    )


class Migration(migrations.Migration):
    dependencies = [('fleet', '0009_alert_build_cancelled')]

    operations = [
        migrations.AlterField(
            model_name='site', name='latitude', field=latitude('Latitude')
        ),
        migrations.AlterField(
            model_name='site', name='longitude', field=longitude('Longitude')
        ),
        migrations.AlterField(
            model_name='deployment', name='latitude', field=latitude('Latitude')
        ),
        migrations.AlterField(
            model_name='deployment', name='longitude', field=longitude('Longitude')
        ),
        migrations.AlterField(
            model_name='deployment',
            name='last_latitude',
            field=latitude('Last Latitude'),
        ),
        migrations.AlterField(
            model_name='deployment',
            name='last_longitude',
            field=longitude('Last Longitude'),
        ),
        migrations.AlterField(
            model_name='positionfix',
            name='latitude',
            field=latitude('Latitude', nullable=False),
        ),
        migrations.AlterField(
            model_name='positionfix',
            name='longitude',
            field=longitude('Longitude', nullable=False),
        ),
    ]
