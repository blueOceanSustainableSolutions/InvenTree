"""Add the creation date to deployments (used for the "no deploy date" reminder)."""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add Deployment.creation_date."""

    dependencies = [('fleet', '0002_default_fault_codes')]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='creation_date',
            field=models.DateField(
                auto_now_add=True,
                default=django.utils.timezone.now,
                verbose_name='Creation Date',
            ),
            preserve_default=False,
        )
    ]
