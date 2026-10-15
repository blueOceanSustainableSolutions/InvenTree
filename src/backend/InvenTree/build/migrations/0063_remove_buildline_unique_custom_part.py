"""Allow duplicate custom parts on build order lines."""

from django.db import migrations


class Migration(migrations.Migration):
    """Allow a part to appear on multiple custom lines in one build order."""

    dependencies = [('build', '0062_quickbuild_quickbuildline')]

    operations = [
        migrations.RemoveConstraint(
            model_name='buildline',
            name='buildline_unique_custom_part',
        )
    ]
