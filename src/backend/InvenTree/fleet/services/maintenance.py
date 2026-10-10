"""Maintenance tasks: lifecycle, component actions, consumables and verification.

Physical changes go through the existing stock functions: component actions
wrap stock.components.change_components (unchanged), and consumables use
StockItem.take_stock. Every stock tracking row written for a task is tagged
with deltas['maintenance'] = task.pk.

Tasks do not need a site or a position: a workshop task has no deployment,
and a deployed device may have neither (section 2.10 of FLEET_PLAN.md).
"""

from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import structlog
from rest_framework.exceptions import ValidationError as DRFValidationError

from fleet import notify
from fleet.events import trigger_status_event
from fleet.helpers import get_model_setting
from fleet.models import (
    Alert,
    ChecklistResult,
    DataStream,
    Deployment,
    DeviceLink,
    FleetDeviceType,
    MaintenanceAction,
    MaintenanceTask,
    TaskType,
)
from fleet.services import deployment as deployment_service
from fleet.services import device_state, monitoring
from fleet.status_codes import (
    AlertStatusGroups,
    DataStreamStatus,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TaskStatusGroups,
    TripStatus,
    TripStatusGroups,
)
from stock.components import change_components
from stock.models import StockItem, StockItemTracking, StockLocation
from stock.status_codes import StockHistoryCode

logger = structlog.get_logger('inventree')

# Component actions of stock.components.change_components, as maintenance actions
COMPONENT_ACTIONS = {
    'add': MaintenanceAction.Action.ADD,
    'remove': MaintenanceAction.Action.REMOVE,
    'destroy': MaintenanceAction.Action.DESTROY,
    'replace': MaintenanceAction.Action.REPLACE,
}

# Actions which are only recorded (no stock change)
NOTE_ACTIONS = [
    MaintenanceAction.Action.REPAIR,
    MaintenanceAction.Action.CLEAN,
    MaintenanceAction.Action.FIRMWARE,
    MaintenanceAction.Action.OTHER,
]


# Name of the trip kit child location which receives removed components
REMOVED_LOCATION_NAME = 'Removed'

# Task types whose completion also clears the PM_DUE / PM_OVERDUE alerts
PM_TASK_TYPES = [TaskType.PREVENTIVE, TaskType.DEPLOYMENT, TaskType.SWAP]

# Task types which put a pipeline deployment in the water
DEPLOY_TASK_TYPES = [TaskType.DEPLOYMENT, TaskType.SWAP]

# Trip statuses in which tasks can be added
TRIP_PLANNABLE_STATUSES = [
    TripStatus.PLANNING.value,
    TripStatus.KIT_READY.value,
    TripStatus.IN_PROGRESS.value,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def task_name(task: MaintenanceTask) -> str:
    """Return how a task's device is shown: the site nickname, else the serial."""
    site = task.site or (task.deployment.site if task.deployment else None)

    if site is not None:
        return site.name

    return task.device.serial or str(task.device)


def get_device_type(task: MaintenanceTask) -> FleetDeviceType | None:
    """Return the device type of a task's device.

    The deployment's device type, else the device type of the device part or
    of its nearest template part.
    """
    if task.deployment is not None:
        return task.deployment.device_type

    parts = task.device.part.get_ancestors(include_self=True, ascending=True)

    for part in parts:
        device_type = FleetDeviceType.objects.filter(part=part).first()

        if device_type is not None:
            return device_type

    return None


def lock_task(task: MaintenanceTask) -> MaintenanceTask:
    """Lock the task row, and refresh the caller's instance."""
    MaintenanceTask.objects.select_for_update().filter(pk=task.pk).first()
    task.refresh_from_db()
    return task


def require_in_progress(task: MaintenanceTask) -> None:
    """Raise unless the task has been started (and not finished)."""
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise ValidationError(_('Start the task first'))


def require_open(task: MaintenanceTask) -> None:
    """Raise if the task is completed or cancelled."""
    if task.status not in TaskStatusGroups.OPEN:
        raise ValidationError(_('This task is closed'))


def add_technician(task: MaintenanceTask, user) -> None:
    """Record a user as a technician of the task."""
    if user is not None and user.is_authenticated:
        task.technicians.add(user)


def tag_tracking(task: MaintenanceTask, after_pk: int, item_ids) -> int:
    """Tag the new stock tracking rows of these items with the task.

    Arguments:
        task: The maintenance task
        after_pk: Only rows created after this tracking pk
        item_ids: Stock items which were involved

    Returns:
        The number of tagged rows
    """
    rows = StockItemTracking.objects.filter(
        pk__gt=after_pk, item__in=[pk for pk in item_ids if pk]
    )

    count = 0

    for row in rows:
        row.deltas = {**(row.deltas or {}), 'maintenance': task.pk}
        row.save(update_fields=['deltas'])
        count += 1

    return count


def last_tracking_pk() -> int:
    """Return the highest stock tracking pk."""
    return StockItemTracking.objects.aggregate(top=Max('pk'))['top'] or 0


def task_note(task: MaintenanceTask, note: str = '') -> str:
    """Prefix a stock note with the task reference."""
    return f'[{task.reference}] {note}'.strip()


def jsonable(value):
    """Return a JSON serializable copy of a verify result."""
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]

    if isinstance(value, datetime | date):
        return value.isoformat()

    return value


def get_removed_location(task: MaintenanceTask) -> StockLocation | None:
    """Return the default location for components removed during a task.

    The "Removed" child of the trip kit location (created on first use), else
    FLEET_WORKSHOP_LOCATION.
    """
    trip = task.trip

    if trip is not None and trip.kit_location is not None:
        location = trip.kit_location.children.filter(name=REMOVED_LOCATION_NAME).first()

        if location is None:
            location = StockLocation.objects.create(
                name=REMOVED_LOCATION_NAME,
                parent=trip.kit_location,
                structural=False,
                description=str(_('Components removed during')) + f' {trip.reference}',
            )

        return location

    return get_model_setting('FLEET_WORKSHOP_LOCATION')


# ---------------------------------------------------------------------------
# Creating and scheduling tasks
# ---------------------------------------------------------------------------


def validate_device(device: StockItem, deployment: Deployment | None) -> None:
    """A task needs a single serialized unit, matching its deployment."""
    MaintenanceTask.validate_device(device, deployment)


def create_task(
    device: StockItem,
    task_type: str = TaskType.CORRECTIVE,
    deployment: Deployment | None = None,
    site=None,
    description: str = '',
    due_date: date | None = None,
    scheduled_date: date | None = None,
    **kwargs,
) -> MaintenanceTask:
    """Create a maintenance task for a device.

    The deployment defaults to the device's DEPLOYED deployment (a workshop
    task has none), and the site to the deployment's site. The task is
    SCHEDULED when a date is given, otherwise PROPOSED.
    """
    if deployment is None:
        deployment = Deployment.objects.filter(
            device=device, status=DeploymentStatus.DEPLOYED.value
        ).first()

    validate_device(device, deployment)

    if site is None and deployment is not None:
        site = deployment.site

    return MaintenanceTask.objects.create(
        status=TaskStatus.SCHEDULED.value
        if scheduled_date
        else TaskStatus.PROPOSED.value,
        task_type=task_type,
        device=device,
        deployment=deployment,
        site=site,
        description=description,
        due_date=due_date,
        scheduled_date=scheduled_date,
        **kwargs,
    )


def update_schedule(task: MaintenanceTask) -> None:
    """Keep the status in step with the scheduled date (after an edit).

    A proposed task with a date is SCHEDULED; a scheduled task whose date was
    cleared (and which is not on a trip) is PROPOSED again.
    """
    if task.status == TaskStatus.PROPOSED.value and task.scheduled_date:
        task.status = TaskStatus.SCHEDULED.value
    elif (
        task.status == TaskStatus.SCHEDULED.value
        and not task.scheduled_date
        and task.trip is None
    ):
        task.status = TaskStatus.PROPOSED.value
    else:
        return

    task.save()
    trigger_status_event(task)


def task_on_trip(task: MaintenanceTask, trip) -> bool:
    """Whether a task is on another open trip."""
    return (
        task.trip_id is not None
        and task.trip_id != trip.pk
        and task.trip.status in TripStatusGroups.OPEN
    )


@transaction.atomic
def schedule_tasks(trip, tasks) -> list[MaintenanceTask]:
    """Schedule tasks on a field trip.

    Proposed or scheduled tasks become SCHEDULED on the trip. The scheduled
    date becomes the trip start date unless it already falls within the trip.
    A deployment (or swap) task of a pipeline deployment also schedules the
    deployment and adds its device to the trip kit.
    """
    if trip.status not in TRIP_PLANNABLE_STATUSES:
        raise ValidationError(_('Tasks can only be added to an open trip'))

    scheduled = []

    for task in tasks:
        lock_task(task)

        if not task.can_start:
            raise ValidationError(
                _('Only proposed or scheduled tasks can be added to a trip')
                + f' ({task.reference})'
            )

        if task_on_trip(task, trip):
            raise ValidationError(
                _('This task is already on trip')
                + f' {task.trip.reference} ({task.reference})'
            )

        deployment = task.deployment

        if (
            task.task_type in DEPLOY_TASK_TYPES
            and deployment is not None
            and deployment.status in DeploymentStatusGroups.PIPELINE
        ):
            scheduled.append(deployment_service.schedule(deployment, trip))
            continue

        date = task.scheduled_date

        if (
            date is None
            or date < trip.start_date
            or (trip.end_date is not None and date > trip.end_date)
        ):
            task.scheduled_date = trip.start_date

        task.trip = trip
        task.status = TaskStatus.SCHEDULED.value
        task.save()
        trigger_status_event(task)

        scheduled.append(task)

    return scheduled


@transaction.atomic
def unschedule_task(task: MaintenanceTask) -> MaintenanceTask:
    """Take a task which has not started off its trip (PROPOSED again).

    Its kit lines are removed, and a scheduled deployment is READY again.
    """
    lock_task(task)

    if not task.can_start:
        raise ValidationError(
            _('A task which has started cannot be taken off its trip')
            + f' ({task.reference})'
        )

    task.kit_lines.all().delete()

    task.trip = None
    task.scheduled_date = None
    task.status = TaskStatus.PROPOSED.value
    task.save()

    if task.task_type in DEPLOY_TASK_TYPES and task.deployment is not None:
        deployment_service.unschedule(task.deployment)

    return task


@transaction.atomic
def create_from_alert(
    alert: Alert,
    user=None,
    scheduled_date: date | None = None,
    trip=None,
    description: str = '',
) -> MaintenanceTask:
    """Create a CORRECTIVE task for an alert, link it, and acknowledge the alert.

    The task is SCHEDULED when a date or trip is given, otherwise PROPOSED.
    """
    if alert.status not in AlertStatusGroups.OPEN:
        raise ValidationError(_('This alert is already resolved'))

    deployment = alert.deployment

    if deployment is None or deployment.device is None:
        raise ValidationError(_('This alert has no device to work on'))

    if alert.task is not None and alert.task.status in TaskStatusGroups.OPEN:
        raise ValidationError(
            _('This alert already has an open task') + f' ({alert.task.reference})'
        )

    task = MaintenanceTask.objects.create(
        status=TaskStatus.SCHEDULED.value
        if scheduled_date
        else TaskStatus.PROPOSED.value,
        task_type=TaskType.CORRECTIVE,
        description=(description or alert.message)[:250],
        device=deployment.device,
        deployment=deployment,
        site=alert.site or deployment.site,
        scheduled_date=scheduled_date,
        due_date=scheduled_date,
    )

    if trip is not None:
        schedule_tasks(trip, [task])
        task.refresh_from_db()

    task.alerts.add(alert)

    alert.task = task
    alert.save()

    monitoring.acknowledge_alert(alert, user=user)

    return task


# ---------------------------------------------------------------------------
# Executing tasks
# ---------------------------------------------------------------------------


@transaction.atomic
def start(task: MaintenanceTask, user=None) -> MaintenanceTask:
    """Start a task: IN_PROGRESS, and create its checklist from the template.

    The checklist holds the device type items for this task type (items with
    a blank task type apply to every task).
    """
    lock_task(task)

    if not task.can_start:
        raise ValidationError(_('Only a proposed or scheduled task can be started'))

    task.status = TaskStatus.IN_PROGRESS.value
    task.started_at = timezone.now()
    task.started_by = user if user is not None and user.is_authenticated else None
    task.save()

    add_technician(task, user)

    device_type = get_device_type(task)

    if device_type is not None and not task.checklist.exists():
        items = device_type.checklist_items.filter(task_type__in=['', task.task_type])

        ChecklistResult.objects.bulk_create([
            ChecklistResult(
                task=task,
                template_item=item,
                sequence=item.sequence,
                text=item.text,
                required=item.required,
            )
            for item in items.order_by('sequence', 'pk')
        ])

    trigger_status_event(task)

    return task


def get_checklist_result(task: MaintenanceTask, result) -> ChecklistResult | None:
    """Check that a checklist result belongs to the task."""
    if result is not None and result.task_id != task.pk:
        raise ValidationError({
            'checklist_result': _('This checklist item belongs to another task')
        })

    return result


def validate_fault_code(fault_code):
    """Inactive fault codes cannot be used."""
    if fault_code is not None and not fault_code.active:
        raise ValidationError({'fault_code': _('This fault code is not active')})

    return fault_code


@transaction.atomic
def component_action(task: MaintenanceTask, user, data: dict) -> MaintenanceAction:
    """Add, remove, destroy or replace a component of the task's device.

    Wraps stock.components.change_components without changing its behaviour.

    Arguments:
        task: An IN_PROGRESS task
        user: The user performing the action
        data: The component action input (action, component, stock_item,
            quantity, replacement_quantity, location, disposition, notes),
            plus fault_code, checklist_result and disable_stream (all optional)

    The removal location defaults to the trip "Removed" location, else the
    fleet workshop location. disable_stream (remove or destroy) disables a
    data stream of the deployment which the device no longer reports.
    """
    lock_task(task)
    require_in_progress(task)

    fault_code = validate_fault_code(data.get('fault_code'))
    checklist_result = get_checklist_result(task, data.get('checklist_result'))
    stream = data.get('disable_stream')

    action = data['action']
    note = data.get('notes') or ''

    if stream is not None:
        if action not in ['remove', 'destroy']:
            raise ValidationError({
                'disable_stream': _('Only a removal can disable a data stream')
            })

        if task.deployment is None or stream.deployment_id != task.deployment_id:
            raise ValidationError({
                'disable_stream': _('This stream belongs to another deployment')
            })

    request = {
        'action': action,
        'quantity': data.get('quantity', 1),
        'disposition': data.get('disposition') or 'keep',
        'notes': task_note(task, note),
    }

    for field in ['component', 'stock_item', 'replacement_quantity']:
        if data.get(field) is not None:
            request[field] = data[field]

    if action in ['remove', 'replace']:
        location = data.get('location') or get_removed_location(task)

        if location is None:
            raise ValidationError({
                'location': _(
                    'Select a location, or set the fleet workshop location setting'
                )
            })

        if location.structural:
            raise ValidationError({
                'location': _('Stock items cannot be located in structural locations')
            })

        request['location'] = location

    involved = {task.device_id, request.get('component'), request.get('stock_item')}
    after = last_tracking_pk()

    try:
        result = change_components(task.device_id, request, user)
    except DRFValidationError as exc:
        raise ValidationError(exc.detail)

    involved.update([result.get('component'), result.get('installed')])
    tag_tracking(task, after, involved)

    component_out = (
        StockItem.objects.filter(pk=result['component']).first()
        if result.get('component')
        else None
    )
    component_in = (
        StockItem.objects.filter(pk=result['installed']).first()
        if result.get('installed')
        else None
    )

    part = (
        (component_in or component_out).part
        if (component_in or component_out)
        else None
    )

    record = MaintenanceAction.objects.create(
        task=task,
        action=COMPONENT_ACTIONS[action],
        component_out=component_out,
        component_in=component_in,
        part=part,
        quantity=request.get('replacement_quantity', request['quantity'])
        if action == 'replace'
        else request['quantity'],
        disposition='destroyed' if action == 'destroy' else request['disposition'],
        destination=request.get('location'),
        fault_code=fault_code,
        checklist_result=checklist_result,
        note=note[:500],
        created_by=user if user is not None and user.is_authenticated else None,
    )

    if stream is not None:
        disable_stream(task, stream, user, record)

    add_technician(task, user)

    return record


def disable_stream(
    task: MaintenanceTask, stream: DataStream, user, record: MaintenanceAction
) -> None:
    """Disable a data stream which the device no longer reports.

    Its open alerts are resolved by the task.
    """
    stream.enabled = False
    stream.essential = False
    stream.state = DataStreamStatus.UNKNOWN.value
    stream.save()

    record.metadata = {**(record.metadata or {}), 'disabled_stream': stream.key}
    record.save()

    for alert in Alert.objects.filter(stream=stream, status__in=AlertStatusGroups.OPEN):
        monitoring.resolve_alert(
            alert, resolution=Alert.Resolution.TASK, user=user, task=task
        )


@transaction.atomic
def consume(
    task: MaintenanceTask, user, stock_item: StockItem, quantity, note: str = ''
) -> MaintenanceAction:
    """Use a bulk consumable (e.g. O-rings, desiccant) during a task.

    On a trip with a kit location, the stock must come from the kit.
    """
    lock_task(task)
    require_in_progress(task)

    StockItem.objects.select_for_update().filter(pk=stock_item.pk).first()
    stock_item.refresh_from_db()

    if stock_item.serialized:
        raise ValidationError({
            'stock_item': _('Install serialized items as components instead')
        })

    if not stock_item.in_stock:
        raise ValidationError({'stock_item': _('This stock item is not in stock')})

    if quantity <= 0 or quantity > stock_item.quantity:
        raise ValidationError({'quantity': _('Quantity exceeds the available stock')})

    kit = task.trip.kit_location if task.trip else None

    if kit is not None:
        allowed = kit.get_descendants(include_self=True)

        if stock_item.location not in allowed:
            raise ValidationError({
                'stock_item': _('Take consumables from the trip kit')
            })

    part = stock_item.part
    after = last_tracking_pk()

    if not stock_item.take_stock(quantity, user, notes=task_note(task, note)):
        raise ValidationError({'quantity': _('The stock could not be taken')})

    tag_tracking(task, after, [stock_item.pk])

    # The stock item may have been deleted when depleted
    remaining = StockItem.objects.filter(pk=stock_item.pk).first()

    record = MaintenanceAction.objects.create(
        task=task,
        action=MaintenanceAction.Action.CONSUME,
        component_out=remaining,
        part=part,
        quantity=quantity,
        note=note[:500],
        created_by=user if user is not None and user.is_authenticated else None,
    )

    add_technician(task, user)

    return record


@transaction.atomic
def reposition(
    task: MaintenanceTask, user, latitude, longitude, note: str = ''
) -> MaintenanceAction:
    """Move the nominal position (and the geofence centre) of the deployment.

    The old position is kept in the action metadata.
    """
    lock_task(task)
    require_in_progress(task)

    deployment = task.deployment

    if deployment is None or deployment.status != DeploymentStatus.DEPLOYED.value:
        raise ValidationError(_('Only a deployed device can be repositioned'))

    def as_float(value):
        return float(value) if value is not None else None

    old = {
        'latitude': as_float(deployment.latitude),
        'longitude': as_float(deployment.longitude),
    }

    deployment_service.set_position(deployment, latitude, longitude)

    record = MaintenanceAction.objects.create(
        task=task,
        action=MaintenanceAction.Action.REPOSITION,
        note=note[:500],
        metadata={
            'old_position': old,
            'new_position': {
                'latitude': as_float(latitude),
                'longitude': as_float(longitude),
            },
        },
        created_by=user if user is not None and user.is_authenticated else None,
    )

    add_technician(task, user)

    return record


@transaction.atomic
def record_action(
    task: MaintenanceTask,
    user,
    action: str,
    note: str = '',
    fault_code=None,
    checklist_result=None,
    firmware_version: str = '',
) -> MaintenanceAction:
    """Record an action without a stock change (repair, clean, firmware, other).

    A firmware action with a version updates the device's platform link.
    """
    lock_task(task)
    require_in_progress(task)

    if action not in NOTE_ACTIONS:
        raise ValidationError({'action': _('Invalid action')})

    validate_fault_code(fault_code)
    get_checklist_result(task, checklist_result)

    metadata = None

    if action == MaintenanceAction.Action.FIRMWARE and firmware_version:
        link, _created = DeviceLink.objects.get_or_create(stock_item=task.device)
        metadata = {
            'old_firmware': link.firmware_version,
            'new_firmware': firmware_version,
        }
        link.firmware_version = firmware_version
        link.save()

    record = MaintenanceAction.objects.create(
        task=task,
        action=action,
        fault_code=fault_code,
        checklist_result=checklist_result,
        note=note[:500],
        metadata=metadata,
        created_by=user if user is not None and user.is_authenticated else None,
    )

    add_technician(task, user)

    return record


@transaction.atomic
def deploy(
    task: MaintenanceTask,
    user=None,
    latitude=None,
    longitude=None,
    depth_m=None,
    deployed_at=None,
    note: str = '',
) -> MaintenanceAction:
    """Deploy the device of a deployment (or swap) task.

    Calls deployment.deploy(). For a swap, the replaced device is recovered
    into the trip "Removed" location (else the workshop); reconciling the
    trip moves it on to the workshop.
    """
    lock_task(task)
    require_in_progress(task)

    deployment = task.deployment

    if task.task_type not in DEPLOY_TASK_TYPES or deployment is None:
        raise ValidationError(_('Only a deployment or swap task can deploy a device'))

    if deployment.device_id != task.device_id:
        raise ValidationError(_('The deployment is for a different device'))

    replaced = deployment.replaces
    swapped = (
        replaced is not None and replaced.status == DeploymentStatus.DEPLOYED.value
    )

    deployment_service.deploy(
        deployment,
        user=user,
        latitude=latitude,
        longitude=longitude,
        depth_m=depth_m,
        deployed_at=deployed_at,
        recover_location=get_removed_location(task) if swapped else None,
    )

    metadata = {'deployed': deployment.reference}

    if swapped:
        replaced.refresh_from_db()
        metadata['recovered'] = replaced.reference

        if replaced.device is not None:
            replaced.device.refresh_from_db()
            metadata['recovered_device'] = replaced.device.serial
            metadata['recovered_location'] = replaced.device.location_id

    record = MaintenanceAction.objects.create(
        task=task,
        action=MaintenanceAction.Action.OTHER,
        component_out=replaced.device if swapped else None,
        component_in=task.device,
        part=task.device.part,
        quantity=1,
        destination=replaced.device.location if swapped and replaced.device else None,
        note=(note or (str(_('Deployed')) + f' {deployment.reference}'))[:500],
        metadata=metadata,
        created_by=user if user is not None and user.is_authenticated else None,
    )

    add_technician(task, user)

    return record


def needs_verification(task: MaintenanceTask) -> bool:
    """Whether the task must pass "Verify data" (a device in the water).

    A docked device is deployed but on land, so it is not verified.
    """
    return (
        task.deployment is not None
        and task.deployment.status == DeploymentStatus.DEPLOYED.value
        and not device_state.is_docked(task.deployment)
    )


@transaction.atomic
def verify(task: MaintenanceTask, user=None, client=None) -> dict:
    """Check that the device reports fresh data since the task started.

    The result is stored in task.verification.
    """
    lock_task(task)
    require_in_progress(task)

    if not needs_verification(task):
        raise ValidationError(_('Only a deployed device can be verified'))

    result = monitoring.verify_now(
        task.deployment, since=task.started_at, client=client
    )

    task.verification = jsonable(result)
    task.save()

    return result


@transaction.atomic
def complete(
    task: MaintenanceTask,
    user=None,
    labour_minutes: int | None = None,
    summary: str = '',
    override_reason: str = '',
) -> MaintenanceTask:
    """Close a task.

    1. Every required checklist item must be answered.
    2. A deployed device must have passed verification, or an override
       reason is given (which creates a CORRECTIVE follow-up task).
    3. The task is COMPLETED, its alerts are resolved (TASK), an
       acknowledged problem of the device is cleared, the next PM date is
       recalculated, a FLEET_MAINTENANCE tracking entry is added to
       the device, and the completion is notified.
    """
    lock_task(task)
    require_in_progress(task)

    pending = task.checklist.filter(
        required=True, result=ChecklistResult.Result.PENDING
    ).count()

    if pending:
        raise ValidationError(
            _('Answer every required checklist item first') + f' ({pending})'
        )

    override_reason = (override_reason or '').strip()
    passed = bool((task.verification or {}).get('passed'))

    if needs_verification(task) and not passed and not override_reason:
        raise ValidationError({
            'override_reason': _(
                'The data check has not passed. Run "Verify data", or give a reason to close anyway'
            )
        })

    now = timezone.now()

    task.status = TaskStatus.COMPLETED.value
    task.completed_at = now
    task.completed_by = user if user is not None and user.is_authenticated else None

    if labour_minutes is not None:
        task.labour_minutes = labour_minutes

    if summary:
        task.summary = summary

    if needs_verification(task) and not passed:
        task.verification_override_reason = override_reason[:500]

    task.save()

    add_technician(task, user)

    if task.verification_override_reason:
        create_follow_up(task)

    resolve_task_alerts(task, user, passed=passed)

    device_state.clear_problem(
        task.device, str(_('Cleared: task completed')) + f' ({task.reference})', user
    )

    if task.deployment is not None:
        deployment_service.compute_next_pm(task.deployment)

    task.device.add_tracking_entry(
        StockHistoryCode.FLEET_MAINTENANCE,
        user,
        deltas={
            'maintenance': task.pk,
            'deployment': task.deployment_id,
            'task_type': task.task_type,
        },
        notes=task_note(task, task.summary[:200]),
    )

    notify_completed(task)

    trigger_status_event(task)

    return task


def create_follow_up(task: MaintenanceTask) -> MaintenanceTask:
    """Create the CORRECTIVE follow-up of a task closed without verification."""
    return MaintenanceTask.objects.create(
        status=TaskStatus.PROPOSED.value,
        task_type=TaskType.CORRECTIVE,
        description=(
            str(_('Follow-up of'))
            + f' {task.reference}: '
            + str(_('data check not passed'))
            + f' ({task.verification_override_reason})'
        )[:250],
        device=task.device,
        deployment=task.deployment,
        site=task.site,
        follow_up_of=task,
    )


def resolve_task_alerts(task: MaintenanceTask, user, passed: bool) -> int:
    """Resolve (TASK) the alerts which a completed task addresses.

    - The alerts linked to the task
    - When the data check passed: the open monitoring alerts of the deployment
      (one whose condition persists is opened again by the next poll)
    - For a preventive (or deployment / swap) task: the PM alerts
    """
    alerts = set(task.alerts.filter(status__in=AlertStatusGroups.OPEN))

    if task.deployment is not None:
        types = []

        if passed:
            types += monitoring.MONITORING_ALERT_TYPES

        if task.task_type in PM_TASK_TYPES:
            types += [Alert.AlertType.PM_DUE, Alert.AlertType.PM_OVERDUE]

        if types:
            alerts.update(
                Alert.objects.filter(
                    deployment=task.deployment,
                    alert_type__in=types,
                    status__in=AlertStatusGroups.OPEN,
                )
            )

    for alert in alerts:
        monitoring.resolve_alert(
            alert, resolution=Alert.Resolution.TASK, user=user, task=task
        )
        task.alerts.add(alert)

    return len(alerts)


def describe_item(item: StockItem | None) -> str:
    """Return a short description of a component (part and serial)."""
    if item is None:
        return '-'

    text = item.part.full_name

    if item.serial:
        text += f' #{item.serial}'

    return text


def action_summary(task: MaintenanceTask) -> list[str]:
    """Return one line per action of the task (for notifications and reports)."""
    lines = []

    for action in task.actions.select_related(
        'component_in__part', 'component_out__part', 'part'
    ):
        label = action.get_action_display()

        if action.action == MaintenanceAction.Action.REPLACE:
            text = (
                f'{label}: {describe_item(action.component_out)} -> '
                f'{describe_item(action.component_in)}'
            )
        elif action.action in [
            MaintenanceAction.Action.REMOVE,
            MaintenanceAction.Action.DESTROY,
        ]:
            text = f'{label}: {describe_item(action.component_out)}'
        elif action.action == MaintenanceAction.Action.ADD:
            text = f'{label}: {describe_item(action.component_in)}'
        elif action.action == MaintenanceAction.Action.CONSUME:
            quantity = f'{action.quantity:g}' if action.quantity is not None else ''
            text = (
                f'{label}: {quantity} x {action.part.full_name if action.part else "-"}'
            )
        else:
            text = label

        if action.note:
            text += f' ({action.note})'

        lines.append(text)

    return lines


def notify_completed(task: MaintenanceTask) -> None:
    """Notify the completion of a task (Teams, email, in-app)."""
    lines = action_summary(task)

    text = task.summary or ''

    if lines:
        text = (text + '\n\n' if text else '') + '\n'.join(
            f'- {line}' for line in lines
        )

    notify.notify(
        'task_completed',
        f'{task.reference} ' + str(_('completed')) + f' - {task_name(task)}',
        text,
        link=notify.build_link(f'task/{task.pk}', obj=task),
        severity='INFO',
        obj=task,
        facts=[
            (_('Task'), task.reference),
            (_('Type'), task.get_task_type_display()),
            (_('Device'), task.device.serial),
            (_('Deployment'), task.deployment.reference if task.deployment else None),
            (_('Actions'), len(lines)),
            (
                _('Verification'),
                _('Passed')
                if (task.verification or {}).get('passed')
                else task.verification_override_reason or None,
            ),
        ],
    )


@transaction.atomic
def cancel(task: MaintenanceTask, user=None, reason: str = '') -> MaintenanceTask:
    """Cancel an open task. Stock changes already made are kept."""
    lock_task(task)

    if not task.can_cancel:
        raise ValidationError(_('This task is closed'))

    task.status = TaskStatus.CANCELLED.value

    if reason:
        task.summary = (
            (task.summary + '\n' if task.summary else '')
            + str(_('Cancelled'))
            + f': {reason}'
        )

    task.save()
    trigger_status_event(task)

    return task
