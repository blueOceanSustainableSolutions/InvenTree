"""Field work entered later: a task is completed on the date the work was done.

- New MaintenanceTask.recorded_at: when the completion was entered
  (completed_at is when the work was done, and can be earlier)

Data fix of the tasks completed before this change, whose completed_at is
when they were entered:
- recorded_at = the old completed_at
- A deployment or swap task which deployed its device takes the deployment
  date (deployed_at), and started_at follows when it was later
- The device replaced by a swap was recovered when the new one went in
- The next PM date of the deployments changed is calculated again (it counts
  from the last completed PM / deployment / swap task), and their proposed
  preventive task follows it
"""

from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone

# Values of fleet.status_codes / fleet.models at the time of this migration
TASK_COMPLETED = 40
TASK_PROPOSED = 10
DEPLOYMENT_DEPLOYED = 50
DEPLOYMENT_RECOVERED = 60
DEPLOY_TASK_TYPES = ['DEPLOYMENT', 'SWAP']
PM_BASE_TASK_TYPES = ['PREVENTIVE', 'DEPLOYMENT', 'SWAP']
NO_SERVICE = 'NO_SERVICE'


def to_local_date(value):
    """The local date of a datetime (aware or naive)."""
    if timezone.is_aware(value):
        return timezone.localtime(value).date()

    return value.date()


def next_pm(deployment, Task):
    """fleet.services.deployment.compute_next_pm, on the historical models."""
    if deployment.coverage == NO_SERVICE:
        return None

    last_task = (
        Task.objects.filter(
            deployment=deployment,
            status=TASK_COMPLETED,
            task_type__in=PM_BASE_TASK_TYPES,
            completed_at__isnull=False,
        )
        .order_by('-completed_at')
        .first()
    )

    base = last_task.completed_at if last_task else deployment.deployed_at

    if base is None:
        return None

    interval = deployment.site.pm_interval_override_days if deployment.site else None
    interval = interval or deployment.device_type.pm_interval_days

    return to_local_date(base) + timedelta(days=interval)


def fix_completion_dates(apps, schema_editor):
    """Date the completed tasks with when the work was done."""
    Task = apps.get_model('fleet', 'MaintenanceTask')
    Deployment = apps.get_model('fleet', 'Deployment')

    completed = Task.objects.filter(status=TASK_COMPLETED, completed_at__isnull=False)

    completed.filter(recorded_at__isnull=True).update(
        recorded_at=models.F('completed_at')
    )

    changed = set()

    tasks = completed.filter(
        task_type__in=DEPLOY_TASK_TYPES,
        deployment__deployed_at__isnull=False,
        deployment__status__in=[DEPLOYMENT_DEPLOYED, DEPLOYMENT_RECOVERED],
    ).select_related('deployment', 'deployment__replaces')

    for task in tasks:
        deployment = task.deployment
        deployed_at = deployment.deployed_at

        # Completed before the device went in: the task did not deploy it
        if deployed_at > task.completed_at:
            continue

        if task.completed_at != deployed_at:
            task.completed_at = deployed_at

            if task.started_at is None or task.started_at > deployed_at:
                task.started_at = deployed_at

            task.save(update_fields=['completed_at', 'started_at'])
            changed.add(deployment.pk)

        replaced = deployment.replaces

        if (
            task.task_type == 'SWAP'
            and replaced is not None
            and replaced.status == DEPLOYMENT_RECOVERED
            and replaced.recovered_at is not None
            and replaced.recovered_at > deployed_at
        ):
            replaced.recovered_at = deployed_at
            replaced.save(update_fields=['recovered_at'])

    deployments = Deployment.objects.filter(
        pk__in=changed, status=DEPLOYMENT_DEPLOYED, next_pm_manual=False
    ).select_related('site', 'device_type')

    for deployment in deployments:
        date = next_pm(deployment, Task)

        if date == deployment.next_pm_date:
            continue

        deployment.next_pm_date = date
        deployment.save(update_fields=['next_pm_date'])

        if date is not None:
            Task.objects.filter(
                deployment=deployment, task_type='PREVENTIVE', status=TASK_PROPOSED
            ).update(due_date=date)


class Migration(migrations.Migration):

    dependencies = [('fleet', '0012_carry_over')]

    operations = [
        migrations.AddField(
            model_name='maintenancetask',
            name='recorded_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the completion was entered',
                null=True,
                verbose_name='Recorded At',
            ),
        ),
        migrations.AlterField(
            model_name='maintenancetask',
            name='completed_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the work was done',
                null=True,
                verbose_name='Completed At',
            ),
        ),
        migrations.RunPython(fix_completion_dates, reverse_code=migrations.RunPython.noop),
    ]
