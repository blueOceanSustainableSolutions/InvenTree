"""A deployment planned by hand (New Deployment) is marked, so the plan list
can tell it from the rows which enter the pipeline on their own.

The deployments planned before this change are not marked: there is no
record of how they were planned.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('fleet', '0013_task_recorded_at')]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='manually_planned',
            field=models.BooleanField(
                default=False,
                help_text='The deployment was planned by hand with New Deployment',
                verbose_name='Manually Planned',
            ),
        )
    ]
