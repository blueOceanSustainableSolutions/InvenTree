"""The fleet plan: one list of deployments and maintenance, in date order.

- Deployment rows: the devices which are not deployed (in production or
  built) and belong to a sale, from the pipeline deployments
- Maintenance rows: the deployed devices which the fleet team services
  (FULL coverage; not docked or decommissioned)

The rows are ordered in three groups: first the next FLEET_PLAN_HORIZON_DAYS
days (overdue included) by date, then the rows without a date, then the
later dates. Rows on the same date (and all the rows without a date) are
ordered by readiness: ready devices first, then in production, then planned,
then maintenance; within each, by the build order target date.
"""

from datetime import date, timedelta

from django.db.models import Q

from common.settings import get_global_setting
from fleet.models import Coverage, Deployment, MaintenanceTask
from fleet.services import device_state
from fleet.services.maintenance import DEPLOY_TASK_TYPES
from fleet.services.pipeline import readiness_risks, sold_q
from fleet.status_codes import (
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TripStatusGroups,
)
from InvenTree.helpers import current_date

KIND_DEPLOYMENT = 'deployment'
KIND_MAINTENANCE = 'maintenance'

GROUP_SOON = 'soon'
GROUP_UNDATED = 'undated'
GROUP_LATER = 'later'

GROUP_ORDER = {GROUP_SOON: 0, GROUP_UNDATED: 1, GROUP_LATER: 2}

# Readiness order of the deployment rows (a scheduled device is built, as a
# ready one); maintenance rows come after them
READINESS_ORDER = {
    DeploymentStatus.READY.value: 0,
    DeploymentStatus.SCHEDULED.value: 0,
    DeploymentStatus.IN_PRODUCTION.value: 1,
    DeploymentStatus.PLANNED.value: 2,
}
MAINTENANCE_ORDER = 3

# Tasks which are still to be done
OPEN_TASK_STATUSES = [
    TaskStatus.PROPOSED.value,
    TaskStatus.SCHEDULED.value,
    TaskStatus.IN_PROGRESS.value,
]


def get_group(row_date: date | None, today: date, horizon: date) -> str:
    """The group of a row: within the horizon, without a date, or later."""
    if row_date is None:
        return GROUP_UNDATED

    return GROUP_SOON if row_date <= horizon else GROUP_LATER


def task_date(task: MaintenanceTask) -> date | None:
    """The date of a task: scheduled, else due."""
    return task.scheduled_date or task.due_date


def trip_of(tasks) -> MaintenanceTask | None:
    """The first task which is on an open trip."""
    for task in tasks:
        if task.trip is not None and task.trip.status in TripStatusGroups.OPEN:
            return task

    return None


def common_fields(deployment: Deployment) -> dict:
    """The fields of a row which come from the deployment."""
    device = deployment.device
    site = deployment.site

    return {
        'pk': deployment.pk,
        'deployment': deployment.pk,
        'reference': deployment.reference,
        'status': deployment.status,
        'device': device.pk if device else None,
        'serial': device.serial if device else None,
        'part_name': device.part.full_name
        if device
        else deployment.device_type.part.full_name,
        'site': site.pk if site else None,
        'site_name': site.name if site else None,
        'client_name': deployment.client.name if deployment.client else None,
    }


def deployment_row(deployment: Deployment, today: date) -> dict:
    """A row for a device which is not deployed."""
    build = deployment.build_order
    tasks = [
        task
        for task in deployment.tasks.all()
        if task.status in OPEN_TASK_STATUSES and task.task_type in DEPLOY_TASK_TYPES
    ]
    trip_task = trip_of(tasks)

    return {
        'kind': KIND_DEPLOYMENT,
        'date': deployment.target_date,
        **common_fields(deployment),
        'build': build.pk if build else None,
        'build_reference': build.reference if build else None,
        'build_title': build.title if build else None,
        'build_status': build.status if build else None,
        'build_target_date': build.target_date if build else None,
        'return_order': getattr(deployment, 'return_order', None),
        'return_order_reference': getattr(deployment, 'return_order_reference', None),
        'task_count': len(tasks),
        'trip': trip_task.trip.pk if trip_task else None,
        'trip_reference': trip_task.trip.reference if trip_task else None,
        'health': None,
        'next_pm_date': None,
        'next_pm_manual': False,
        'risks': readiness_risks(deployment, today=today),
    }


def maintenance_row(deployment: Deployment) -> dict:
    """A row for a deployed device: its next maintenance."""
    tasks = [
        task
        for task in deployment.tasks.all()
        if task.status in OPEN_TASK_STATUSES and task.task_type not in DEPLOY_TASK_TYPES
    ]
    trip_task = trip_of(tasks)

    dates = [
        value for value in [*map(task_date, tasks), deployment.next_pm_date] if value
    ]

    return {
        'kind': KIND_MAINTENANCE,
        'date': min(dates) if dates else None,
        **common_fields(deployment),
        'build': None,
        'build_reference': None,
        'build_title': None,
        'build_status': None,
        'build_target_date': None,
        'return_order': None,
        'return_order_reference': None,
        'task_count': len(tasks),
        'trip': trip_task.trip.pk if trip_task else None,
        'trip_reference': trip_task.trip.reference if trip_task else None,
        'health': deployment.health,
        'next_pm_date': deployment.next_pm_date,
        'next_pm_manual': deployment.next_pm_manual,
        'risks': [],
    }


def plan_rows(
    today: date | None = None,
    kind: str | None = None,
    site: int | None = None,
    search: str | None = None,
) -> list[dict]:
    """Return the rows of the fleet plan, in plan order.

    Arguments:
        today: The current date (for tests)
        kind: Only 'deployment' or 'maintenance' rows
        site: Only the rows of this site
        search: Text in the reference, serial, part, site or client
    """
    from fleet.serializers import DeploymentSerializer

    today = today or current_date()
    horizon = today + timedelta(
        days=get_global_setting('FLEET_PLAN_HORIZON_DAYS', cache=False)
    )

    queryset = Deployment.objects.select_related(
        'site',
        'client',
        'build',
        'device',
        'device__build',
        'device__part',
        'device_type__part',
    ).prefetch_related('tasks', 'tasks__trip')

    if site:
        queryset = queryset.filter(site=site)

    if search:
        queryset = queryset.filter(
            Q(reference__icontains=search)
            | Q(device__serial__icontains=search)
            | Q(device_type__part__name__icontains=search)
            | Q(site__name__icontains=search)
            | Q(site__reference__icontains=search)
            | Q(client__name__icontains=search)
        )

    rows = []

    if kind in [None, '', KIND_DEPLOYMENT]:
        pipeline = DeploymentSerializer.annotate_queryset(
            queryset.filter(status__in=DeploymentStatusGroups.PIPELINE).filter(sold_q())
        )
        rows += [deployment_row(deployment, today) for deployment in pipeline]

    if kind in [None, '', KIND_MAINTENANCE]:
        deployed = (
            queryset.filter(status=DeploymentStatus.DEPLOYED.value)
            .filter(coverage=Coverage.FULL)
            .exclude(device_state.hidden_q())
        )
        rows += [maintenance_row(deployment) for deployment in deployed]

    for row in rows:
        row['group'] = get_group(row['date'], today, horizon)

    rows.sort(key=sort_key)

    return rows


def sort_key(row: dict) -> tuple:
    """The plan order of a row: group, date, readiness, build target date."""
    if row['kind'] == KIND_DEPLOYMENT:
        readiness = READINESS_ORDER.get(row['status'], MAINTENANCE_ORDER)
    else:
        readiness = MAINTENANCE_ORDER

    return (
        GROUP_ORDER[row['group']],
        row['date'] or date.max,
        readiness,
        row['build_target_date'] or date.max,
        row['reference'],
    )
