"""Maintenance planning: preventive maintenance proposals and planning alerts.

Run daily by the fleet_daily_planning task, and on demand. Nothing here needs
a site or a position: the PM interval comes from the device type unless the
site has an override (see deployment.compute_next_pm).
"""

from datetime import date, timedelta

from django.db import transaction
from django.utils.translation import gettext_lazy as _

import structlog

from common.settings import get_global_setting
from fleet import notify
from fleet.events import trigger_status_events
from fleet.models import (
    Alert,
    Coverage,
    Deployment,
    KitMode,
    MaintenanceAction,
    MaintenanceTask,
    TaskType,
    TripKitLine,
)
from fleet.services.device_state import hidden_q
from fleet.services.monitoring import (
    alert_facts,
    alert_link,
    alert_title,
    open_or_update_alert,
    resolve_alert,
)
from fleet.services.pipeline import readiness_risks
from fleet.status_codes import (
    AlertSeverity,
    AlertStatusGroups,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TaskStatusGroups,
)
from InvenTree.helpers import current_date

logger = structlog.get_logger('inventree')

# Alert types raised and cleared by pm_alerts()
PM_ALERT_TYPES = [Alert.AlertType.PM_DUE, Alert.AlertType.PM_OVERDUE]

# Alert types raised and cleared by pipeline_alerts() (the readiness risk codes)
PIPELINE_ALERT_TYPES = [
    Alert.AlertType.BUILD_LATE,
    Alert.AlertType.NOT_READY,
    Alert.AlertType.PARTS_SHORT,
    Alert.AlertType.NO_DEPLOY_DATE,
]

# Pipeline alerts which are notified (Teams, email, in-app) when they open
NOTIFIED_PIPELINE_TYPES = [Alert.AlertType.BUILD_LATE, Alert.AlertType.NOT_READY]

# Kit suggestion: a part which failed in at least FAILURE_MIN_TASKS of the last
# FAILURE_HISTORY_TASKS completed tasks of a device type is LIKELY needed
FAILURE_HISTORY_TASKS = 10
FAILURE_MIN_TASKS = 2

# Actions which take a component out because it failed (with a fault code)
FAILURE_ACTIONS = [
    MaintenanceAction.Action.REPLACE,
    MaintenanceAction.Action.REMOVE,
    MaintenanceAction.Action.DESTROY,
]


def task_device_name(deployment: Deployment) -> str:
    """Return how a deployment is shown in planning: site nickname, else serial."""
    if deployment.site:
        return deployment.site.name

    if deployment.device:
        return deployment.device.serial or str(deployment.device)

    return deployment.reference


def pm_deployments():
    """Return the DEPLOYED deployments which get preventive maintenance.

    Docked (and decommissioned) devices are left out.
    """
    return (
        Deployment.objects
        .filter(
            status=DeploymentStatus.DEPLOYED.value,
            coverage=Coverage.FULL,
            device__isnull=False,
        )
        .exclude(hidden_q())
        .select_related('site', 'device', 'device_type')
    )


def propose_pm_tasks(today: date | None = None) -> list[MaintenanceTask]:
    """Propose a preventive maintenance task for each deployment due soon.

    A deployment gets a PROPOSED PREVENTIVE task when its next PM date is
    within FLEET_PLAN_HORIZON_DAYS and it has no open preventive task. The due
    date of a task which is still only proposed follows the next PM date.

    Returns:
        The tasks which were created
    """
    today = today or current_date()
    horizon = today + timedelta(
        days=get_global_setting('FLEET_PLAN_HORIZON_DAYS', cache=False)
    )
    created = []

    for deployment in pm_deployments().filter(
        next_pm_date__isnull=False, next_pm_date__lte=horizon
    ):
        open_tasks = deployment.tasks.filter(
            task_type=TaskType.PREVENTIVE, status__in=TaskStatusGroups.OPEN
        )

        if open_tasks.exists():
            open_tasks.filter(status=TaskStatus.PROPOSED.value).exclude(
                due_date=deployment.next_pm_date
            ).update(due_date=deployment.next_pm_date)
            continue

        created.append(
            MaintenanceTask.objects.create(
                status=TaskStatus.PROPOSED.value,
                task_type=TaskType.PREVENTIVE,
                description=str(_('Preventive maintenance')),
                device=deployment.device,
                deployment=deployment,
                site=deployment.site,
                due_date=deployment.next_pm_date,
            )
        )

    return created


def cancel_stale_proposals() -> int:
    """Cancel proposed preventive tasks which no longer apply.

    That is when the deployment has ended (recovered or cancelled), no
    longer gets preventive maintenance (coverage changed), or its device is
    docked (or decommissioned).

    Returns:
        The number of cancelled tasks
    """
    proposed = MaintenanceTask.objects.filter(
        status=TaskStatus.PROPOSED.value,
        task_type=TaskType.PREVENTIVE,
        deployment__isnull=False,
    )

    ended = proposed.exclude(
        deployment__status=DeploymentStatus.DEPLOYED.value,
        deployment__coverage=Coverage.FULL,
    ).values_list('pk', flat=True)

    hidden = proposed.filter(
        hidden_q('deployment__device__fleet_link__state')
    ).values_list('pk', flat=True)

    pks = set(ended) | set(hidden)
    count = MaintenanceTask.objects.filter(pk__in=pks).update(
        status=TaskStatus.CANCELLED.value, status_custom_key=None
    )

    trigger_status_events('maintenancetask', pks, TaskStatus.CANCELLED.value)

    return count


def resolve_cleared(alert_types, active_keys: set) -> int:
    """Resolve (AUTO) the open alerts of these types whose condition has cleared."""
    count = 0

    for alert in Alert.objects.filter(
        alert_type__in=alert_types, status__in=AlertStatusGroups.OPEN
    ).exclude(dedupe_key__in=active_keys):
        resolve_alert(alert, resolution=Alert.Resolution.AUTO)
        count += 1

    return count


def pm_alerts(today: date | None = None) -> dict:
    """Raise or clear the preventive maintenance alerts.

    - PM_DUE (INFO): the next PM date is within FLEET_PM_DUE_WARNING_DAYS
    - PM_OVERDUE (WARNING): the next PM date has passed
    - Deployments with NO_SERVICE coverage get none, nor do docked devices

    Returns:
        {'raised': n, 'resolved': n}
    """
    today = today or current_date()
    warning = today + timedelta(
        days=get_global_setting('FLEET_PM_DUE_WARNING_DAYS', cache=False)
    )
    active_keys = set()

    deployments = (
        Deployment.objects
        .filter(
            status=DeploymentStatus.DEPLOYED.value,
            next_pm_date__isnull=False,
            next_pm_date__lte=warning,
        )
        .exclude(coverage=Coverage.NO_SERVICE)
        .exclude(hidden_q())
        .select_related('site', 'device')
    )

    for deployment in deployments:
        name = task_device_name(deployment)
        due = deployment.next_pm_date

        if due < today:
            alert_type = Alert.AlertType.PM_OVERDUE
            severity = AlertSeverity.WARNING.value
            message = (
                str(_('Preventive maintenance overdue'))
                + f': {name} ('
                + str(_('due'))
                + f' {due.isoformat()})'
            )
        else:
            alert_type = Alert.AlertType.PM_DUE
            severity = AlertSeverity.INFO.value
            message = (
                str(_('Preventive maintenance due')) + f': {name} ({due.isoformat()})'
            )

        key = f'dp:{deployment.pk}:{alert_type}'
        active_keys.add(key)

        open_or_update_alert(
            key,
            alert_type,
            severity,
            message,
            deployment=deployment,
            site=deployment.site,
            data={'next_pm_date': due.isoformat()},
        )

    resolved = resolve_cleared(PM_ALERT_TYPES, active_keys)

    return {'raised': len(active_keys), 'resolved': resolved}


def pipeline_alerts(today: date | None = None) -> dict:
    """Raise or clear alerts for the readiness risks of pipeline deployments.

    BUILD_LATE and NOT_READY are notified (Teams, email, in-app) when they
    open. A CRITICAL alert is notified by open_or_update_alert itself (on open
    and on escalation), so it is not notified twice.

    Returns:
        {'raised': n, 'notified': n, 'resolved': n}
    """
    today = today or current_date()
    active_keys = set()
    notified = 0

    deployments = Deployment.objects.filter(
        status__in=DeploymentStatusGroups.PIPELINE
    ).select_related('site', 'device', 'build')

    for deployment in deployments:
        for risk in readiness_risks(deployment, today=today):
            key = f'dp:{deployment.pk}:{risk["code"]}'
            active_keys.add(key)

            alert, event = open_or_update_alert(
                key,
                risk['code'],
                risk['severity'],
                risk['message'],
                deployment=deployment,
                site=deployment.site,
            )

            if (
                event == 'opened'
                and risk['code'] in NOTIFIED_PIPELINE_TYPES
                and risk['severity'] != AlertSeverity.CRITICAL.value
            ):
                notify_pipeline_alert(alert)
                notified += 1

    resolved = resolve_cleared(PIPELINE_ALERT_TYPES, active_keys)

    return {'raised': len(active_keys), 'notified': notified, 'resolved': resolved}


def notify_pipeline_alert(alert: Alert) -> None:
    """Notify a pipeline alert which is not CRITICAL (e.g. BUILD_LATE)."""
    deployment = alert.deployment
    facts = alert_facts(alert)

    if deployment is not None:
        facts.append((_('Target Date'), deployment.target_date))

        if deployment.build:
            facts.append((_('Build Order'), deployment.build.reference))

    notify.notify(
        'pipeline_risk',
        alert_title(alert),
        alert.message,
        link=alert_link(alert),
        severity=alert.severity,
        obj=alert,
        facts=facts,
    )


def failed_parts(device_type) -> dict:
    """Parts which failed in several recent tasks of a device type.

    Counts the completed tasks (the last FAILURE_HISTORY_TASKS of the device
    type) with a replace / remove / destroy action that has a fault code.

    Returns:
        {part: number of tasks} for parts with at least FAILURE_MIN_TASKS
    """
    recent = list(
        MaintenanceTask.objects
        .filter(status=TaskStatus.COMPLETED.value, deployment__device_type=device_type)
        .order_by('-completed_at', '-pk')
        .values_list('pk', flat=True)[:FAILURE_HISTORY_TASKS]
    )

    counts = {}

    for part, _task in (
        MaintenanceAction.objects
        .filter(
            task__in=recent,
            action__in=FAILURE_ACTIONS,
            fault_code__isnull=False,
            part__isnull=False,
        )
        .values_list('part', 'task')
        .distinct()
    ):
        counts[part] = counts.get(part, 0) + 1

    return {part: n for part, n in counts.items() if n >= FAILURE_MIN_TASKS}


def suggest_kit(trip, apply: bool = True) -> list[dict]:
    """Suggest the parts kit of a field trip.

    For each open task on the trip:
    - for a deployment (or swap) task of a pipeline deployment: the device
      itself (one line with its serial), and nothing else;
    - for the other tasks: the device type kit lines (ALWAYS and LIKELY),
      once per task;
    and for each device type, the parts which failed in several recent tasks
    (LIKELY, quantity 1).

    The suggestion replaces the lines which were not added by hand (MANUAL).
    The availability is computed by trips.kit_lines().

    Returns:
        The suggested lines (dicts), applied as TripKitLines unless apply=False
    """
    from fleet.services import maintenance, trips

    lines = {}
    device_types = {}

    tasks = (
        trip.tasks
        .filter(status__in=TaskStatusGroups.OPEN)
        .select_related('device__part', 'deployment__device_type', 'site')
        .order_by('reference_int')
    )

    for task in tasks:
        name = maintenance.task_name(task)
        deployment = task.deployment

        if (
            task.task_type in maintenance.DEPLOY_TASK_TYPES
            and deployment is not None
            and deployment.status in DeploymentStatusGroups.PIPELINE
        ):
            trips.add_line(
                lines,
                task.device.part,
                1,
                TripKitLine.Source.DEVICE,
                note=deployment.reference,
                stock_item=task.device,
                task=task,
            )

            # A new device: the maintenance kit of the type is not needed
            continue

        device_type = maintenance.get_device_type(task)

        if device_type is None:
            continue

        device_types[device_type.pk] = device_type

        for template in device_type.kit_lines.select_related('part'):
            source = (
                TripKitLine.Source.ALWAYS
                if template.mode == KitMode.ALWAYS
                else TripKitLine.Source.LIKELY
            )
            trips.add_line(lines, template.part, template.quantity, source, note=name)

    for device_type in device_types.values():
        failed = failed_parts(device_type)

        if not failed:
            continue

        from part.models import Part

        for part in Part.objects.filter(pk__in=failed.keys()):
            note = (
                str(_('Failed in'))
                + f' {failed[part.pk]}/{FAILURE_HISTORY_TASKS} '
                + str(_('recent tasks'))
            )
            key = (part.pk, TripKitLine.Source.LIKELY, None)

            if key in lines:
                lines[key]['notes'].append(note)
            else:
                trips.add_line(lines, part, 1, TripKitLine.Source.LIKELY, note=note)

    suggestion = list(lines.values())

    if apply:
        trips.apply_kit_suggestion(trip, suggestion)

    return suggestion


def run_daily_planning(today: date | None = None) -> dict:
    """Run every planning step (each in its own transaction).

    A failing step is logged, and does not stop the others.

    Returns:
        A summary dict per step
    """
    summary = {}

    steps = [
        ('cancelled', cancel_stale_proposals),
        ('proposed', lambda: len(propose_pm_tasks(today=today))),
        ('pm_alerts', lambda: pm_alerts(today=today)),
        ('pipeline_alerts', lambda: pipeline_alerts(today=today)),
    ]

    for name, step in steps:
        try:
            with transaction.atomic():
                summary[name] = step()
        except Exception:
            logger.exception('Fleet planning: step %s failed', name)
            summary[name] = None

    return summary
