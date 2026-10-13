"""The site is the only location: deployments lose their own position.

- New SiteLocationChange: the location history of a site, with the reason
- Deployment latitude, longitude, depth and geofence are removed (their values
  are discarded; the site location is where the device should be)
- distance_from_nominal_m is renamed distance_from_site_m
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def depth():
    """A water depth field (m)."""
    return models.DecimalField(
        blank=True,
        decimal_places=2,
        help_text='Water depth (m)',
        max_digits=8,
        null=True,
        validators=[django.core.validators.MinValueValidator(0)],
        verbose_name='Depth',
    )


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
    dependencies = [
        ('fleet', '0010_coordinate_precision'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteLocationChange',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'effective_at',
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        help_text='When the device was actually moved',
                        verbose_name='Effective At',
                    ),
                ),
                ('old_latitude', latitude('Old Latitude')),
                ('old_longitude', longitude('Old Longitude')),
                ('old_depth_m', depth()),
                ('latitude', latitude('Latitude', nullable=False)),
                ('longitude', longitude('Longitude', nullable=False)),
                ('depth_m', depth()),
                (
                    'comment',
                    models.CharField(
                        help_text='Why the site was moved',
                        max_length=500,
                        verbose_name='Comment',
                    ),
                ),
                (
                    'created_at',
                    models.DateTimeField(auto_now_add=True, verbose_name='Created At'),
                ),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Created By',
                    ),
                ),
                (
                    'site',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='location_changes',
                        to='fleet.site',
                        verbose_name='Site',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Site Location Change',
                'verbose_name_plural': 'Site Location Changes',
                'ordering': ['site', '-effective_at', '-pk'],
            },
        ),
        migrations.RemoveField(model_name='deployment', name='latitude'),
        migrations.RemoveField(model_name='deployment', name='longitude'),
        migrations.RemoveField(model_name='deployment', name='depth_m'),
        migrations.RemoveField(model_name='deployment', name='geofence_radius_m'),
        migrations.RemoveField(model_name='deployment', name='geofence_polygon'),
        migrations.RenameField(
            model_name='deployment',
            old_name='distance_from_nominal_m',
            new_name='distance_from_site_m',
        ),
        migrations.AlterField(
            model_name='deployment',
            name='distance_from_site_m',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Distance of the reported position from the site location (m)',
                max_digits=12,
                null=True,
                verbose_name='Distance From Site',
            ),
        ),
        migrations.AlterField(
            model_name='maintenanceaction',
            name='action',
            field=models.CharField(
                choices=[
                    ('REPLACE', 'Replace'),
                    ('REMOVE', 'Remove'),
                    ('ADD', 'Add'),
                    ('DESTROY', 'Destroy'),
                    ('CONSUME', 'Consume'),
                    ('REPAIR', 'Repair'),
                    ('CLEAN', 'Clean'),
                    ('FIRMWARE', 'Firmware'),
                    ('REPOSITION', 'Move Site'),
                    ('OTHER', 'Other'),
                ],
                max_length=20,
                verbose_name='Action',
            ),
        ),
    ]
