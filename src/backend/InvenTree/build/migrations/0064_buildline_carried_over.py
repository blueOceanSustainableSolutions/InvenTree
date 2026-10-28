"""Stock which another build order hands over covers a build line."""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add BuildLine.carried_over (pending carry-over counts as allocated)."""

    dependencies = [('build', '0063_remove_buildline_unique_custom_part')]

    operations = [
        migrations.AddField(
            model_name='buildline',
            name='carried_over',
            field=models.DecimalField(
                decimal_places=5,
                default=0,
                help_text='Quantity covered by stock consumed by another build order, which will be moved to this build order',
                max_digits=15,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name='Carried Over',
            ),
        )
    ]
