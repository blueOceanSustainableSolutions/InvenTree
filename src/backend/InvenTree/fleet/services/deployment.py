"""Deploying and recovering devices.

A device is "deployed" when its stock item is assigned to a customer (the
legacy "deployed = has customer" convention), and recovering it returns the
item from the customer into a stock location.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fleet.events import trigger_status_event, trigger_status_events
from fleet.helpers import get_model_setting, to_local_date
from fleet.models import (
    Alert,
    Coverage,
    DataStream,
    Deployment,
    MaintenanceTask,
    TripKitLine,
)
from fleet.services.device_state import check_not_decommissioned, clear_on_close
from fleet.services.pipeline import ensure_device_link
from fleet.status_codes import (
    AlertStatus,
    AlertStatusGroups,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TripStatusGroups,
)
from stock.status_codes import StockHistoryCode

# Fields changed by set_position()
POSITION_FIELDS = ['latitude', 'longitude', 'depth_m', 'geofence_radius_m']

# Completed task types which restart the preventive maintenance interval
PM_BASE_TASK_TYPES = [
    MaintenanceTask.TaskType.PREVENTIVE,
    MaintenanceTask.TaskType.DEPLOYMENT,
    MaintenanceTask.TaskType.SWAP,
]


def get_customer(deployment: Deployment):
    """Return the customer to assign a deployed device to.

    The deployment client, else the site client, else FLEET_DEFAULT_CUSTOMER.
    """
    if deployment.client:
        return deployment.client

    if deployment.site and deployment.site.client:
        return deployment.site.client

    return get_model_setting('FLEET_DEFAULT_CUSTOMER')


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
    deployment: Deployment,
    user=None,
    latitude=None,
    longitude=None,
    depth_m=None,
    deployed_at=None,
    recover_location=None,
) -> Deployment:
    """Deploy the device of a READY (or SCHEDULED) deployment.

    The site is optional. Without one the position comes only from the
    arguments (or the deployment), and may stay empty until it is set.

    - Records the nominal position and copies the site geofence if empty
    - Copies the stream templates into data streams
    - Assigns the stock item to a customer (tracking code 100) and adds a
      FLEET_DEPLOYED tracking entry
    - Recovers the deployment it replaces, if any (into recover_location,
      e.g. the trip "Removed" location; default the workshop)
    - Computes the next preventive maintenance date
    """
    # Lock the row, and work on the caller's instance so that it stays current
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if not deployment.can_deploy:
        raise ValidationError(_('Only a ready or scheduled deployment can be deployed'))

    device = deployment.device
    site = deployment.site

    if device is None:
        raise ValidationError(_('No device has been assigned to this deployment'))

    if device.customer is not None or device.is_building or not device.in_stock:
        raise ValidationError(_('The device must be in stock to be deployed'))

    check_not_decommissioned(device)

    customer = get_customer(deployment)

    if customer is None:
        raise ValidationError(
            _(
                'No customer: set a client on the deployment or its site, or the default fleet customer setting'
            )
        )

    # Recover the device being replaced first: a site has one active deployment
    replaced = deployment.replaces

    if replaced is not None and replaced.status == DeploymentStatus.DEPLOYED.value:
        recover(
            replaced,
            user=user,
            location=recover_location,
            notes=str(_('Replaced by')) + f' {deployment.reference}',
        )

    if site is not None:
        active = (
            Deployment.objects
            .filter(site=site, status=DeploymentStatus.DEPLOYED.value)
            .exclude(pk=deployment.pk)
            .first()
        )

        if active is not None:
            raise ValidationError({
                'site': _('The site already has an active deployment')
                + f' ({active.reference})'
            })

    # Position: given, else the one already on the deployment, else the site's
    if latitude is not None and longitude is not None:
        deployment.latitude = latitude
        deployment.longitude = longitude
    elif (
        deployment.latitude is None or deployment.longitude is None
    ) and site is not None:
        deployment.latitude = site.latitude
        deployment.longitude = site.longitude

    if depth_m is not None:
        deployment.depth_m = depth_m
    elif deployment.depth_m is None and site is not None:
        deployment.depth_m = site.depth_m

    if site is not None:
        if deployment.geofence_radius_m is None:
            deployment.geofence_radius_m = site.geofence_radius_m

        if deployment.geofence_polygon is None:
            deployment.geofence_polygon = site.geofence_polygon

    deployment.client = customer
    deployment.deployed_at = deployed_at or timezone.now()
    deployment.status = DeploymentStatus.DEPLOYED.value
    deployment.save()

    copy_streams(deployment)
    ensure_device_link(device)

    notes = f'Deployed {deployment.reference}'

    if site is not None:
        notes += f' at {site.reference}'

    device.allocateToCustomer(customer, user=user, notes=notes)

    device.refresh_from_db()
    device.add_tracking_entry(
        StockHistoryCode.FLEET_DEPLOYED,
        user,
        deltas={'deployment': deployment.pk, 'site': site.pk if site else None},
        notes=notes,
    )

    compute_next_pm(deployment)

    trigger_status_event(deployment)

    return deployment


@transaction.atomic
def recover(deployment: Deployment, user=None, location=None, notes: str = ''):
    """Recover a deployed device into a stock location.

    The location defaults to FLEET_WORKSHOP_LOCATION. Open alerts of the
    deployment are resolved automatically.
    """
    if not deployment.can_recover:
        raise ValidationError(_('Only a deployed device can be recovered'))

    if location is None:
        location = get_model_setting('FLEET_WORKSHOP_LOCATION')

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

    now = timezone.now()

    deployment.status = DeploymentStatus.RECOVERED.value
    deployment.recovered_at = now
    deployment.save()

    text = f'Recovered {deployment.reference}'

    if deployment.site:
        text += f' from {deployment.site.reference}'

    if notes:
        text += f': {notes}'

    if device := deployment.device:
        if device.customer is not None:
            device.return_from_customer(location, user, notes=text)
            device.refresh_from_db()

        device.add_tracking_entry(
            StockHistoryCode.FLEET_RECOVERED,
            user,
            deltas={
                'deployment': deployment.pk,
                'site': deployment.site.pk if deployment.site else None,
                'location': location.pk,
            },
            notes=text,
        )

    resolve_open_alerts(deployment, user=user, now=now)
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
    """Schedule a READY deployment on a field trip.

    The deployment becomes SCHEDULED, and gets a DEPLOYMENT task on the trip
    (a SWAP task when it replaces a deployed device at its site). The device
    is added to the trip kit. An open deployment task which already exists
    (e.g. from an earlier trip) is reused.

    Returns:
        The deployment (or swap) task
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if not deployment.can_schedule:
        raise ValidationError(
            _('Only a ready deployment can be scheduled on a trip')
            + f' ({deployment.reference})'
        )

    device = deployment.device

    if device is None:
        raise ValidationError(
            _('No device has been assigned to this deployment')
            + f' ({deployment.reference})'
        )

    check_not_decommissioned(device)

    task = (
        deployment.tasks
        .filter(
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

    deployment.status = DeploymentStatus.SCHEDULED.value
    deployment.save()

    trigger_status_event(task)
    trigger_status_event(deployment)

    TripKitLine.objects.filter(task=task, source=TripKitLine.Source.DEVICE).exclude(
        trip=trip
    ).delete()

    TripKitLine.objects.update_or_create(
        trip=trip,
        task=task,
        source=TripKitLine.Source.DEVICE,
        defaults={
            'part': device.part,
            'stock_item': device,
            'quantity_planned': 1,
            'note': deployment.reference,
        },
    )

    return task


def unschedule(deployment: Deployment) -> Deployment:
    """A SCHEDULED deployment whose task left its trip is READY again."""
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


@transaction.atomic
def set_position(
    deployment: Deployment, latitude, longitude, depth_m=None, geofence_radius_m=None
) -> Deployment:
    """Set the nominal position of a device (and optionally its geofence radius).

    Devices found automatically from the stock have no position until a user
    sets it. Closed deployments cannot be changed.
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if deployment.status in DeploymentStatusGroups.CLOSED:
        raise ValidationError(
            _('The position of a closed deployment cannot be changed')
        )

    if latitude is None or longitude is None:
        raise ValidationError(_('Latitude and longitude are both required'))

    deployment.latitude = latitude
    deployment.longitude = longitude

    if depth_m is not None:
        deployment.depth_m = depth_m

    if geofence_radius_m is not None:
        deployment.geofence_radius_m = geofence_radius_m

    deployment.clean_fields(
        exclude=[
            field.name
            for field in Deployment._meta.fields
            if field.name not in POSITION_FIELDS
        ]
    )
    deployment.save()

    return deployment


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
            deployment.tasks
            .filter(
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
