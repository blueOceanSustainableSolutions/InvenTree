"""Deploying and recovering devices.

A device is "deployed" only through the fleet Deploy action (directly, or by
completing its deployment task). Shipping it to a customer is not deploying
it: a shipped unit is out of stock, but still waits to be deployed.

Every deployed unit goes through a sale: deploying a unit which is not
shipped yet ships it on its own from its sales order (see
fleet.services.sales); a shipped unit keeps the customer it was shipped to. Recovering a device creates a return order
to the office: the device stays with the customer until the office receives
it, but it is no longer deployed, and it can be planned for its next
deployment straight away. A deployed device cannot be returned through the
normal return order or stock menus (see pipeline.check_return_allowed).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fleet.events import trigger_status_event, trigger_status_events
from fleet.helpers import check_field_date, to_local_date
from fleet.models import (
    Alert,
    Coverage,
    DataStream,
    Deployment,
    MaintenanceTask,
    TripKitLine,
)
from fleet.services import sales
from fleet.services.device_state import (
    check_not_decommissioned,
    clear_on_close,
    is_decommissioned,
)
from fleet.services.pipeline import (
    ensure_device_link,
    get_return_line,
    is_shipped,
    is_sold,
)
from fleet.status_codes import (
    AlertStatus,
    AlertStatusGroups,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TaskStatusGroups,
    TripStatusGroups,
)
from stock.status_codes import StockHistoryCode

# Completed task types which restart the preventive maintenance interval
PM_BASE_TASK_TYPES = [
    MaintenanceTask.TaskType.PREVENTIVE,
    MaintenanceTask.TaskType.DEPLOYMENT,
    MaintenanceTask.TaskType.SWAP,
]


def copy_streams(deployment: Deployment) -> None:
    """Copy the stream templates of the device type into the deployment."""
    for template in deployment.device_type.stream_templates.all():
        DataStream.objects.get_or_create(
            deployment=deployment,
            key=template.key,
            defaults={
                'name': template.name,
                'essential': template.essential,
                'expected_interval_minutes': template.expected_interval_minutes,
                'grace_minutes': template.grace_minutes,
            },
        )


@transaction.atomic
def deploy(
    deployment: Deployment, user=None, site=None, deployed_at=None
) -> Deployment:
    """Deploy the device of a READY (or SCHEDULED) deployment.

    A site is required: the given one, else the site already planned on the
    deployment. The device has no position of its own; the location of the
    site is where it should be.

    - Copies the stream templates into data streams
    - Ships a unit in stock on its own from its sales order; a unit already
      shipped keeps its customer. Adds a FLEET_DEPLOYED tracking entry
    - Recovers the deployment it replaces, if any (a return order brings
      that device to the office), on the same date
    - Computes the next preventive maintenance date

    The deployment date can be in the past (the deploy is entered after the
    field work), but not in the future, nor before the replaced deployment.
    """
    # Lock the row, and work on the caller's instance so that it stays current
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if not deployment.can_deploy:
        raise ValidationError(_('Only a ready or scheduled deployment can be deployed'))

    device = deployment.device

    if device is None:
        raise ValidationError(_('No device has been assigned to this deployment'))

    if return_line := get_return_line(device):
        raise ValidationError(
            _('The device is still on its way back. Receive it on return order')
            + f' {return_line.order.reference} '
            + _('first')
        )

    shipped = is_shipped(device)

    if not shipped and (device.is_building or not device.in_stock):
        raise ValidationError(
            _('The device must be in stock, or shipped to a customer, to be deployed')
        )

    check_not_decommissioned(device)

    # A site chosen at deploy time replaces the planned one (and its coverage)
    if site is not None and site.pk != deployment.site_id:
        deployment.site = site
        deployment.coverage = site.coverage

    site = deployment.site

    if site is None:
        raise ValidationError({'site': _('A site is required to deploy a device')})

    if not site.active:
        raise ValidationError({'site': _('The site is not active')})

    # A shipped unit stays with the customer it was shipped to; any other is
    # shipped from its sales order
    sale = None if shipped else sales.get_sale(deployment)
    customer = device.customer if shipped else sale.order.customer

    if customer is None:
        raise ValidationError(
            _('Sales order') + f' {sale.order.reference} ' + _('has no customer')
        )

    # Recover the device being replaced first: a site has one active deployment
    replaced = deployment.replaces
    swapped = (
        replaced is not None and replaced.status == DeploymentStatus.DEPLOYED.value
    )

    deployed_at = (
        check_field_date(
            deployed_at,
            'deployed_at',
            earliest=replaced.deployed_at if swapped else None,
        )
        if deployed_at
        else timezone.now()
    )

    if swapped:
        recover(
            replaced,
            user=user,
            notes=str(_('Replaced by')) + f' {deployment.reference}',
            recovered_at=deployed_at,
        )

    active = (
        Deployment.objects.filter(site=site, status=DeploymentStatus.DEPLOYED.value)
        .exclude(pk=deployment.pk)
        .first()
    )

    if active is not None:
        raise ValidationError(
            {
                'site': _('The site already has an active deployment')
                + f' ({active.reference})'
            }
        )

    deployment.client = customer
    deployment.deployed_at = deployed_at
    deployment.status = DeploymentStatus.DEPLOYED.value
    deployment.save()

    copy_streams(deployment)
    ensure_device_link(device)

    notes = f'Deployed {deployment.reference} at {site.reference}'

    if sale is not None:
        sales.ship(sale, deployment, user=user)

    device.add_tracking_entry(
        StockHistoryCode.FLEET_DEPLOYED,
        user,
        deltas={'deployment': deployment.pk, 'site': site.pk},
        notes=notes,
    )

    compute_next_pm(deployment)

    trigger_status_event(deployment)

    return deployment


def create_return_order(deployment: Deployment, device, user=None, notes: str = ''):
    """Create (and issue) the return order which brings a recovered device to the office.

    The office receives the line on the return order, which takes the device
    back from the customer into a stock location.
    """
    from order.models import ReturnOrder, ReturnOrderLineItem

    description = str(_('Fleet recovery')) + f' {deployment.reference}'

    if deployment.site:
        description += f' ({deployment.site.name})'

    order = ReturnOrder.objects.create(
        reference=ReturnOrder.generate_reference(),
        customer=device.customer,
        description=description[:250],
        created_by=user if user is not None and user.is_authenticated else None,
    )

    ReturnOrderLineItem.objects.create(
        order=order, item=device, quantity=1, notes=notes[:500]
    )

    order.issue_order()

    return order


@transaction.atomic
def recover(deployment: Deployment, user=None, notes: str = '', recovered_at=None):
    """Recover a deployed device: it goes back to the office on a return order.

    recovered_at is when the device left the water (defaults to now): a swap
    recovers the replaced device on the date of the new deployment.

    - The deployment is RECOVERED and its open alerts are resolved
    - A return order (issued) brings the device to the office; the office
      receives it into a stock location
    - The device gets its next (READY) deployment row straight away, so it
      can be planned while it is on its way back (it can only be deployed
      once it has been received)
    """
    from fleet.services.pipeline import ensure_unit_deployment

    if not deployment.can_recover:
        raise ValidationError(_('Only a deployed device can be recovered'))

    now = timezone.now()

    deployment.status = DeploymentStatus.RECOVERED.value
    deployment.recovered_at = recovered_at or now
    deployment.save()

    text = f'Recovered {deployment.reference}'

    if deployment.site:
        text += f' from {deployment.site.reference}'

    if notes:
        text += f': {notes}'

    if device := deployment.device:
        return_order = None

        if device.customer is not None:
            return_order = create_return_order(deployment, device, user, notes=text)

        device.add_tracking_entry(
            StockHistoryCode.FLEET_RECOVERED,
            user,
            deltas={
                'deployment': deployment.pk,
                'site': deployment.site.pk if deployment.site else None,
                'returnorder': return_order.pk if return_order else None,
            },
            notes=text,
        )

    resolve_open_alerts(deployment, user=user, now=now)
    clear_on_close(deployment.device, user=user)

    trigger_status_event(deployment)

    if deployment.device is not None and not is_decommissioned(deployment.device):
        ensure_unit_deployment(deployment.device, deployment.device_type)

    return deployment


def deployed_by_fleet(deployment: Deployment) -> bool:
    """True if the fleet Deploy action deployed this deployment (FLEET_DEPLOYED entry).

    Deployments opened by the old stock sync (a customer on the stock item,
    e.g. a sales order shipment) have no such entry.
    """
    from stock.models import StockItemTracking

    if deployment.device_id is None:
        return False

    return StockItemTracking.objects.filter(
        item_id=deployment.device_id,
        tracking_type=StockHistoryCode.FLEET_DEPLOYED.value,
        deltas__deployment=deployment.pk,
    ).exists()


@transaction.atomic
def undo_deploy(deployment: Deployment, user=None) -> Deployment:
    """Put a DEPLOYED deployment back to READY: the device is not in the water.

    For devices which the old stock sync marked as deployed because they were
    shipped. Nothing moves in the stock: a shipped unit keeps its customer,
    and waits for the fleet Deploy action. The open alerts are resolved.
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if deployment.status != DeploymentStatus.DEPLOYED.value:
        raise ValidationError(_('Only a deployed device can be marked as not deployed'))

    if deployment.device is None:
        raise ValidationError(_('No device has been assigned to this deployment'))

    if open_task := deployment.tasks.filter(status__in=TaskStatusGroups.OPEN).first():
        raise ValidationError(
            _('The deployment still has an open task') + f' ({open_task.reference})'
        )

    deployment.status = DeploymentStatus.READY.value
    deployment.deployed_at = None
    deployment.recovered_at = None

    if not deployment.next_pm_manual:
        deployment.next_pm_date = None

    deployment.save()

    resolve_open_alerts(deployment, user=user)
    clear_on_close(deployment.device, user=user)

    trigger_status_event(deployment)

    return deployment


def deployment_task_type(deployment: Deployment) -> str:
    """The task which puts a deployment in the water: SWAP for a replacement."""
    replaced = deployment.replaces

    if replaced is not None and replaced.status == DeploymentStatus.DEPLOYED.value:
        return MaintenanceTask.TaskType.SWAP

    return MaintenanceTask.TaskType.DEPLOYMENT


@transaction.atomic
def schedule(deployment: Deployment, trip) -> MaintenanceTask:
    """Schedule a deployment on a field trip.

    The deployment gets a DEPLOYMENT task on the trip (a SWAP task when it
    replaces a deployed device at its site). An open deployment task which
    already exists (e.g. from an earlier trip) is reused.

    - A READY device: the deployment becomes SCHEDULED, and the device is
      added to the trip kit.
    - A device which is not built yet (PLANNED, IN_PRODUCTION): the
      deployment keeps its status (so NOT_READY still warns). The task has no
      device until the build output exists; see attach_unit().

    Returns:
        The deployment (or swap) task
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if not deployment.can_schedule:
        raise ValidationError(
            _('Only a deployment which is not deployed can be scheduled on a trip')
            + f' ({deployment.reference})'
        )

    # Deploying ships the unit from its sale: plan only the units sold
    if not is_sold(deployment):
        raise ValidationError(
            _('The device is not on a sales order')
            + f' ({deployment.reference}). '
            + _('Allocate it to a sales order before planning its deployment')
        )

    device = deployment.device
    built = deployment.status not in DeploymentStatusGroups.UNBUILT

    if device is None and built:
        raise ValidationError(
            _('No device has been assigned to this deployment')
            + f' ({deployment.reference})'
        )

    if device is not None:
        check_not_decommissioned(device)

    task = (
        deployment.tasks.filter(
            status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value],
            task_type__in=[
                MaintenanceTask.TaskType.DEPLOYMENT,
                MaintenanceTask.TaskType.SWAP,
            ],
        )
        .order_by('pk')
        .first()
    )

    if (
        task is not None
        and task.trip is not None
        and task.trip != trip
        and task.trip.status in TripStatusGroups.OPEN
    ):
        raise ValidationError(
            _('This deployment is already on trip')
            + f' {task.trip.reference} ({deployment.reference})'
        )

    if task is None:
        task_type = deployment_task_type(deployment)

        if task_type == MaintenanceTask.TaskType.SWAP:
            description = (
                str(_('Swap'))
                + f': {deployment.replaces.reference} -> {deployment.reference}'
            )
        else:
            description = str(_('Deploy')) + f' {deployment.reference}'

        if deployment.site:
            description += f' ({deployment.site.name})'

        task = MaintenanceTask(
            task_type=task_type,
            description=description[:250],
            device=device,
            deployment=deployment,
            site=deployment.site,
            due_date=deployment.target_date,
        )

    task.trip = trip
    task.device = device
    task.scheduled_date = trip.start_date
    task.status = TaskStatus.SCHEDULED.value
    task.save()

    trigger_status_event(task)

    if built:
        deployment.status = DeploymentStatus.SCHEDULED.value
        deployment.save()
        trigger_status_event(deployment)

    TripKitLine.objects.filter(task=task, source=TripKitLine.Source.DEVICE).exclude(
        trip=trip
    ).delete()

    add_device_kit_line(task)

    return task


def add_device_kit_line(task: MaintenanceTask) -> None:
    """Put the device of a deployment task in its trip kit (once it has one)."""
    if task.trip is None or task.device is None:
        return

    TripKitLine.objects.update_or_create(
        trip=task.trip,
        task=task,
        source=TripKitLine.Source.DEVICE,
        defaults={
            'part': task.device.part,
            'stock_item': task.device,
            'quantity_planned': 1,
            'note': task.deployment.reference if task.deployment else '',
        },
    )


def open_deploy_tasks(deployment: Deployment):
    """The deployment (or swap) tasks of a deployment which have not started."""
    return deployment.tasks.filter(
        status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value],
        task_type__in=[
            MaintenanceTask.TaskType.DEPLOYMENT,
            MaintenanceTask.TaskType.SWAP,
        ],
    )


def attach_unit(deployment: Deployment) -> None:
    """Catch up the tasks of a deployment which was scheduled before it was built.

    Called when the build output is linked, and when the device is READY:
    - its open deployment tasks get the device, and the device goes in the
      trip kit
    - a READY deployment with a task on an open trip is SCHEDULED
    """
    if deployment.device is None:
        return

    on_trip = False

    for task in open_deploy_tasks(deployment).select_related('trip'):
        if task.device_id is None:
            task.device = deployment.device
            task.save()

        if task.trip is not None and task.trip.status in TripStatusGroups.OPEN:
            on_trip = True
            add_device_kit_line(task)

    if on_trip and deployment.status == DeploymentStatus.READY.value:
        deployment.status = DeploymentStatus.SCHEDULED.value
        deployment.save()
        trigger_status_event(deployment)


def unschedule(deployment: Deployment) -> Deployment:
    """A SCHEDULED deployment whose task left its trip is READY again.

    The swap will not happen on that trip: the parts picked to carry over are
    released (they can be picked again for the next trip).
    """
    from fleet.services import carry_over

    carry_over.release(deployment)

    if deployment.status == DeploymentStatus.SCHEDULED.value:
        deployment.status = DeploymentStatus.READY.value
        deployment.save()
        trigger_status_event(deployment)

    return deployment


def resolve_open_alerts(deployment: Deployment, user=None, now=None) -> int:
    """Resolve (AUTO) the open alerts of a deployment which has ended."""
    alerts = Alert.objects.filter(
        deployment=deployment, status__in=AlertStatusGroups.OPEN
    )
    pks = list(alerts.values_list('pk', flat=True))

    count = alerts.update(
        status=AlertStatus.RESOLVED.value,
        status_custom_key=None,
        resolved_at=now or timezone.now(),
        resolved_by=user,
        resolution=Alert.Resolution.AUTO,
    )

    trigger_status_events('alert', pks, AlertStatus.RESOLVED.value)

    return count


def compute_next_pm(deployment: Deployment, commit: bool = True):
    """Compute the next preventive maintenance date of a deployment.

    - Skipped when the date was set by hand (next_pm_manual)
    - None for NO_SERVICE coverage
    - Otherwise the last completed PREVENTIVE / DEPLOYMENT / SWAP task (or the
      deployment date) plus the site override or device type interval
    """
    if deployment.next_pm_manual:
        return deployment.next_pm_date

    next_pm = None

    if deployment.coverage != Coverage.NO_SERVICE:
        last_task = (
            deployment.tasks.filter(
                status=TaskStatus.COMPLETED.value,
                task_type__in=PM_BASE_TASK_TYPES,
                completed_at__isnull=False,
            )
            .order_by('-completed_at')
            .first()
        )

        base = last_task.completed_at if last_task else deployment.deployed_at

        if base is not None:
            interval = None

            if deployment.site:
                interval = deployment.site.pm_interval_override_days

            interval = interval or deployment.device_type.pm_interval_days

            next_pm = to_local_date(base) + timedelta(days=interval)

    deployment.next_pm_date = next_pm

    if commit:
        deployment.save()

    return next_pm


@transaction.atomic
def set_next_pm_date(
    deployment: Deployment, next_pm_date=None, automatic: bool = False
):
    """Set a manual PM date, or return to the calculated maintenance cycle."""
    if deployment.status != DeploymentStatus.DEPLOYED.value:
        raise ValidationError(_('Preventive maintenance applies to deployed devices'))

    if deployment.coverage != Coverage.FULL:
        raise ValidationError(
            _('This deployment does not receive preventive maintenance')
        )

    if automatic:
        deployment.next_pm_manual = False
        compute_next_pm(deployment)
    else:
        if next_pm_date is None:
            raise ValidationError(_('Enter the next preventive maintenance date'))

        deployment.next_pm_date = next_pm_date
        deployment.next_pm_manual = True
        deployment.save(update_fields=['next_pm_date', 'next_pm_manual'])

    # Import here because planning imports compute_next_pm from this module.
    from fleet.services.planning import sync_pm_task

    sync_pm_task(deployment)
    return deployment
