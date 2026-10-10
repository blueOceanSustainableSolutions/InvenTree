"""Internal device states (section 2.11 of FLEET_PLAN.md).

Each device shows one state in the fleet lists. Three are set by a person and
stored on the device link, so they belong to the physical device and outlive
its deployments and the stock sync:

- DECOMMISSIONED: will not become active again; its deployment is closed and
  the stock sync never reopens it
- DOCKED: back on land, still with the customer: not polled, not planned, not
  on the map and not in the overview KPIs
- PROBLEM_ACKNOWLEDGED: monitoring goes on, but its notifications are muted

The others are worked out from the deployment: MAINTENANCE_SCHEDULED (a task
on an open field trip), MAINTENANCE_OVERDUE (PM past due), UNRESPONSIVE (no
data for FLEET_NO_CONTACT_HOURS) and ACTIVE. The first match in the
DeviceState order wins.

The other services are imported inside the functions which need them: they
import this module themselves.
"""

from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import (
    Case,
    CharField,
    Exists,
    F,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.settings import get_global_setting
from fleet.events import FleetEvents, trigger_status_events
from fleet.models import (
    Alert,
    Coverage,
    Deployment,
    DeviceLink,
    DeviceState,
    InternalState,
    MaintenanceTask,
    TaskType,
)
from fleet.status_codes import (
    AlertStatus,
    DeploymentStatus,
    DeploymentStatusGroups,
    HealthStatus,
    TaskStatus,
    TaskStatusGroups,
    TripStatusGroups,
)
from InvenTree.helpers import current_date

# Manual states which take a device out of monitoring, planning, map and KPIs
HIDDEN_STATES = [InternalState.DOCKED, InternalState.DECOMMISSIONED]

# Manual states which only make sense while the device is in the water
DEPLOYED_ONLY_STATES = [InternalState.PROBLEM_ACKNOWLEDGED, InternalState.DOCKED]

# Manual states cleared when the deployment of the device is closed
CLEARED_ON_CLOSE = [InternalState.PROBLEM_ACKNOWLEDGED, InternalState.DOCKED]


def hidden_q(field: str = 'device__fleet_link__state') -> Q:
    """Q for the devices which are DOCKED or DECOMMISSIONED.

    Arguments:
        field: Path to DeviceLink.state from the queried model
    """
    return Q(**{f'{field}__in': HIDDEN_STATES})


def decommissioned_q(field: str = 'device__fleet_link__state') -> Q:
    """Q for the devices which are DECOMMISSIONED."""
    return Q(**{field: InternalState.DECOMMISSIONED})


def annotate_device_state(
    queryset: QuerySet, today: date | None = None, now: datetime | None = None
) -> QuerySet:
    """Annotate a Deployment queryset with its effective `device_state`.

    Pipeline and closed deployments get None, unless the device is
    DECOMMISSIONED.
    """
    today = today or current_date()
    now = now or timezone.now()
    hours = max(1, get_global_setting('FLEET_NO_CONTACT_HOURS', cache=False))

    state = 'device__fleet_link__state'

    trip_task = MaintenanceTask.objects.filter(
        device=OuterRef('device'),
        status__in=TaskStatusGroups.OPEN,
        trip__status__in=TripStatusGroups.OPEN,
    )

    return queryset.annotate(
        state_contact_since=Coalesce(F('last_contact'), F('deployed_at'))
    ).annotate(
        device_state=Case(
            When(
                Q(**{state: InternalState.DECOMMISSIONED}),
                then=Value(DeviceState.DECOMMISSIONED.value),
            ),
            When(
                ~Q(status=DeploymentStatus.DEPLOYED.value),
                then=Value(None, output_field=CharField()),
            ),
            When(
                Q(**{state: InternalState.DOCKED}), then=Value(DeviceState.DOCKED.value)
            ),
            When(
                Exists(trip_task), then=Value(DeviceState.MAINTENANCE_SCHEDULED.value)
            ),
            When(
                Q(next_pm_date__lt=today) & ~Q(coverage=Coverage.NO_SERVICE),
                then=Value(DeviceState.MAINTENANCE_OVERDUE.value),
            ),
            When(
                Q(**{state: InternalState.PROBLEM_ACKNOWLEDGED}),
                then=Value(DeviceState.PROBLEM_ACKNOWLEDGED.value),
            ),
            When(
                Q(device__fleet_link__platform_id__gt='')
                & Q(state_contact_since__lt=now - timedelta(hours=hours)),
                then=Value(DeviceState.UNRESPONSIVE.value),
            ),
            default=Value(DeviceState.ACTIVE.value),
            output_field=CharField(),
        )
    )


def device_state(deployment: Deployment) -> str | None:
    """Return the effective state of one deployment (see annotate_device_state)."""
    return (
        annotate_device_state(Deployment.objects.filter(pk=deployment.pk))
        .values_list('device_state', flat=True)
        .first()
    )


def get_state(device) -> str:
    """Return the manual state of a device ('' when none or no link)."""
    if device is None:
        return InternalState.NONE

    return (
        DeviceLink.objects
        .filter(stock_item=device)
        .values_list('state', flat=True)
        .first()
        or InternalState.NONE
    )


def is_decommissioned(device) -> bool:
    """Whether a device is DECOMMISSIONED."""
    return get_state(device) == InternalState.DECOMMISSIONED


def check_not_decommissioned(device) -> None:
    """Refuse an action on a DECOMMISSIONED device."""
    if is_decommissioned(device):
        raise ValidationError(
            _('This device is decommissioned. Clear its state first')
            + f' (#{device.serial})'
        )


def is_docked(deployment: Deployment | None) -> bool:
    """Whether the device of a deployment is DOCKED."""
    return deployment is not None and get_state(deployment.device) == (
        InternalState.DOCKED
    )


def is_muted(deployment: Deployment | None) -> bool:
    """Whether the notifications about a deployment are muted.

    They are while its device is PROBLEM_ACKNOWLEDGED.
    """
    if deployment is None or deployment.device_id is None:
        return False

    return DeviceLink.objects.filter(
        stock_item_id=deployment.device_id, state=InternalState.PROBLEM_ACKNOWLEDGED
    ).exists()


def change_state(link: DeviceLink, state: str, user=None, note: str = '') -> None:
    """Store a new manual state on a device link."""
    link.state = state
    link.state_note = (note or '')[:250]
    link.state_changed_at = timezone.now()
    link.state_changed_by = user if user is not None and user.is_authenticated else None
    link.save()

    from plugin.events import trigger_event

    trigger_event(FleetEvents.DEVICE_STATE_CHANGED, id=link.pk, state=state)


def clear_states(device, states, reason: str, user=None) -> bool:
    """Clear the manual state of a device if it is one of `states`.

    Returns:
        True if the state was cleared
    """
    if device is None:
        return False

    link = DeviceLink.objects.filter(stock_item=device, state__in=states).first()

    if link is None:
        return False

    change_state(link, InternalState.NONE, user=user, note=reason)
    return True


def clear_on_close(device, user=None) -> bool:
    """A closed deployment clears DOCKED and PROBLEM_ACKNOWLEDGED (not DECOMMISSIONED)."""
    return clear_states(
        device, CLEARED_ON_CLOSE, str(_('Cleared: deployment closed')), user=user
    )


def clear_problem(device, reason: str, user=None) -> bool:
    """Clear PROBLEM_ACKNOWLEDGED (device healthy again, or a task completed)."""
    return clear_states(device, [InternalState.PROBLEM_ACKNOWLEDGED], reason, user=user)


@transaction.atomic
def set_state(
    deployment: Deployment, state: str, user=None, note: str = ''
) -> DeviceLink:
    """Set (or clear, with '') the manual state of the device of a deployment.

    - PROBLEM_ACKNOWLEDGED (deployed devices): the open alerts are
      acknowledged; notifications are muted until it clears
    - DOCKED (deployed devices): the open alerts are resolved (AUTO, not
      notified), the health becomes UNKNOWN and a still proposed PM task is
      cancelled; the deployment stays DEPLOYED
    - DECOMMISSIONED: the DEPLOYED deployment is closed (RECOVERED, no stock
      movement) and the tasks which have not started are cancelled. Refused
      for a device in the pipeline or with a task in progress

    Returns:
        The device link
    """
    from fleet.services import deployment as deployment_service
    from fleet.services import maintenance, monitoring
    from fleet.services.pipeline import ensure_device_link

    state = state or InternalState.NONE

    if state not in InternalState.values:
        raise ValidationError({'state': _('Invalid state')})

    device = deployment.device

    if device is None:
        raise ValidationError(_('No device has been assigned to this deployment'))

    link = ensure_device_link(device)
    DeviceLink.objects.select_for_update().filter(pk=link.pk).first()
    link.refresh_from_db()

    deployment.refresh_from_db()
    deployed = deployment.status == DeploymentStatus.DEPLOYED.value

    if state in DEPLOYED_ONLY_STATES and not deployed:
        raise ValidationError({
            'state': _(
                'Only a deployed device can be docked or have a problem acknowledged'
            )
        })

    tasks = MaintenanceTask.objects.filter(device=device)

    if state == InternalState.DECOMMISSIONED:
        if Deployment.objects.filter(
            device=device, status__in=DeploymentStatusGroups.PIPELINE
        ).exists():
            raise ValidationError({
                'state': _(
                    'The device is in a planned deployment: cancel it or assign another device first'
                )
            })

        if tasks.filter(status=TaskStatus.IN_PROGRESS.value).exists():
            raise ValidationError({
                'state': _(
                    'A task on this device is in progress: complete or cancel it first'
                )
            })

    previous = link.state
    change_state(link, state, user=user, note=note)

    if state == previous:
        return link

    if state == InternalState.PROBLEM_ACKNOWLEDGED:
        for alert in Alert.objects.filter(
            deployment=deployment, status=AlertStatus.OPEN.value
        ):
            monitoring.acknowledge_alert(alert, user=user)

    elif state == InternalState.DOCKED:
        deployment_service.resolve_open_alerts(deployment, user=user)

        deployment.health = HealthStatus.UNKNOWN.value
        deployment.save(update_fields=['health'])

        proposed = tasks.filter(
            deployment=deployment,
            status=TaskStatus.PROPOSED.value,
            task_type=TaskType.PREVENTIVE,
        )
        pks = list(proposed.values_list('pk', flat=True))

        proposed.update(status=TaskStatus.CANCELLED.value, status_custom_key=None)
        trigger_status_events('maintenancetask', pks, TaskStatus.CANCELLED.value)

    elif state == InternalState.DECOMMISSIONED:
        reason = str(_('Device decommissioned'))

        for active in Deployment.objects.filter(
            device=device, status=DeploymentStatus.DEPLOYED.value
        ):
            close_decommissioned(active, user=user)

        for task in tasks.filter(
            status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value]
        ):
            if task.trip_id is not None:
                maintenance.unschedule_task(task)

            maintenance.cancel(task, user=user, reason=reason)

    return link


def close_decommissioned(deployment: Deployment, user=None) -> Deployment:
    """Close the DEPLOYED deployment of a decommissioned device.

    No stock movement: the item keeps its customer, and the stock sync leaves
    it closed while the device is DECOMMISSIONED.
    """
    from fleet.services.deployment import resolve_open_alerts

    now = timezone.now()

    deployment.status = DeploymentStatus.RECOVERED.value
    deployment.recovered_at = now
    deployment.metadata = {
        **(deployment.metadata or {}),
        'decommissioned': now.isoformat(),
    }
    deployment.save()

    resolve_open_alerts(deployment, user=user, now=now)

    return deployment
