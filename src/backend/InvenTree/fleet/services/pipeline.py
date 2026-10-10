"""Deployment pipeline: from build order to a device which is ready to deploy.

A deployment enters the pipeline when the build order for a fleet device is
issued (or when it is planned by hand). A build order linked to a sales order
enters it earlier, as PLANNED, as soon as it is created (still PENDING). The
build output becomes the device, and the deployment is READY once the output
is complete.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

import structlog

import InvenTree.helpers
import InvenTree.ready
from build.status_codes import BuildStatus, BuildStatusGroups
from common.settings import get_global_setting
from fleet.events import trigger_status_event
from fleet.models import Alert, Deployment, DeviceLink, FleetDeviceType
from fleet.services.device_state import is_decommissioned
from fleet.status_codes import AlertSeverity, DeploymentStatus, DeploymentStatusGroups

logger = structlog.get_logger('inventree')

# Statuses in which the build output is still expected to become the device
SYNC_STATUSES = [DeploymentStatus.PLANNED.value, DeploymentStatus.IN_PRODUCTION.value]


def get_device_type(part) -> FleetDeviceType | None:
    """Return the active fleet device type for a part, if any."""
    if part is None:
        return None

    return FleetDeviceType.objects.filter(part=part, active=True).first()


def is_fleet_build(build) -> bool:
    """Return True if a build order builds an active fleet device type, or has a deployment."""
    return FleetDeviceType.objects.filter(
        Q(part_id=build.part_id, active=True) | Q(deployments__build=build)
    ).exists()


def create_build_deployment(build, status: int) -> Deployment | None:
    """Create the deployment of a fleet build order (None if not a fleet part)."""
    device_type = get_device_type(build.part)

    if device_type is None:
        return None

    deployment = Deployment(
        status=status,
        device_type=device_type,
        build=build,
        deployment_type=Deployment.DeploymentType.NEW_STATION,
        target_date=None,
    )

    if build.quantity != 1:
        # The product owner says fleet build orders are always quantity 1
        deployment.metadata = {
            'warning': f'Build order {build.reference} has quantity '
            f'{InvenTree.helpers.normalize(build.quantity)}; only one '
            'deployment was created'
        }
        logger.warning(
            'Fleet build order %s has quantity %s', build.reference, build.quantity
        )

    deployment.save()
    trigger_status_event(deployment)

    return deployment


def on_build_saved(build) -> Deployment | None:
    """Keep the deployment of a fleet build order in step with the build.

    Called from the post_save receiver on build.Build. This covers both ways
    a build order is issued (_action_issue and create_build_output), and is
    idempotent. A PENDING build order linked to a sales order gets a PLANNED
    deployment straight away, so sold devices are visible before the build
    order is issued.

    Returns:
        The linked deployment, if there is one
    """
    if InvenTree.ready.isImportingData() or not InvenTree.ready.canAppAccessDatabase(
        allow_test=True
    ):
        return None

    deployment = Deployment.objects.filter(build=build).first()

    if build.status == BuildStatus.PENDING.value:
        if deployment is None and build.sales_order_id:
            deployment = create_build_deployment(build, DeploymentStatus.PLANNED.value)

    elif build.status == BuildStatus.PRODUCTION.value:
        if deployment is None:
            deployment = create_build_deployment(
                build, DeploymentStatus.IN_PRODUCTION.value
            )

        elif deployment.status == DeploymentStatus.PLANNED.value:
            # A planned deployment whose build order has now been issued
            deployment.status = DeploymentStatus.IN_PRODUCTION.value
            deployment.save()
            trigger_status_event(deployment)

    elif deployment is None:
        return None

    elif build.status == BuildStatus.CANCELLED.value:
        if deployment.status in SYNC_STATUSES:
            deployment.status = DeploymentStatus.CANCELLED.value
            deployment.save()
            trigger_status_event(deployment)

    elif build.status == BuildStatus.COMPLETE.value:
        sync_build_output(deployment)

    return deployment


def get_build_output(build):
    """Return the output stock item of a build (completed outputs first)."""
    from stock.models import StockItem

    return StockItem.objects.filter(build=build).order_by('is_building', 'pk').first()


def ensure_device_link(stock_item) -> DeviceLink:
    """Return the DeviceLink of a device, creating it if missing.

    A new link has a blank platform id, which flags it as "not configured".
    """
    link, _created = DeviceLink.objects.get_or_create(stock_item=stock_item)
    return link


def sync_build_output(deployment: Deployment) -> bool:
    """Link the build output to the deployment, and mark it READY once complete.

    Build outputs are bulk-created (no post_save), so this is also run by a
    scheduled task.

    Returns:
        True if the deployment was changed
    """
    build = deployment.build

    if build is None or deployment.status not in SYNC_STATUSES:
        return False

    changed = False
    previous_status = deployment.status

    # The post_save hook may have been missed (e.g. data imported)
    if deployment.status == DeploymentStatus.PLANNED.value and build.status in [
        BuildStatus.PRODUCTION.value,
        BuildStatus.COMPLETE.value,
    ]:
        deployment.status = DeploymentStatus.IN_PRODUCTION.value
        changed = True

    if deployment.device is None:
        if output := get_build_output(build):
            deployment.device = output
            changed = True

    if deployment.device is not None:
        ensure_device_link(deployment.device)

        if (
            deployment.status == DeploymentStatus.IN_PRODUCTION.value
            and not deployment.device.is_building
        ):
            deployment.status = DeploymentStatus.READY.value
            changed = True

    if changed:
        deployment.save()

        if deployment.status != previous_status:
            trigger_status_event(deployment)

    return changed


def sync_sales_builds() -> int:
    """Create the PLANNED deployments of pending fleet build orders for a sale.

    Catches up build orders whose post_save hook was missed (e.g. created
    before the fleet module, or imported).

    Returns:
        The number of deployments which were created
    """
    from build.models import Build

    builds = Build.objects.filter(
        status=BuildStatus.PENDING.value,
        sales_order__isnull=False,
        part__fleet_device_type__active=True,
        fleet_deployment__isnull=True,
    )

    return sum(
        1
        for build in builds
        if create_build_deployment(build, DeploymentStatus.PLANNED.value)
    )


def sync_missed_builds() -> int:
    """Catch up issued and cancelled fleet build orders whose post_save hook was missed.

    The hook is skipped while importing data, and a fleet error in it is only
    logged (see fleet.signals). An issued build order can then lack its
    deployment, and a cancelled one can leave its deployment open.

    Returns:
        The number of deployments which were created or cancelled
    """
    from build.models import Build

    builds = Build.objects.filter(
        status=BuildStatus.PRODUCTION.value,
        part__fleet_device_type__active=True,
        fleet_deployment__isnull=True,
    )

    count = sum(
        1
        for build in builds
        if create_build_deployment(build, DeploymentStatus.IN_PRODUCTION.value)
    )

    for deployment in Deployment.objects.filter(
        status__in=SYNC_STATUSES, build__status=BuildStatus.CANCELLED.value
    ):
        deployment.status = DeploymentStatus.CANCELLED.value
        deployment.save()
        trigger_status_event(deployment)
        count += 1

    return count


def sync_all() -> int:
    """Synchronize every pipeline deployment which has a build order.

    Also creates the deployments of pending sales build orders and issued
    build orders which were missed, and cancels those of cancelled build orders.

    Returns:
        The number of deployments which were created or changed
    """
    count = sync_sales_builds() + sync_missed_builds()

    for deployment in Deployment.objects.filter(
        status__in=SYNC_STATUSES, build__isnull=False
    ).select_related('build', 'device'):
        if sync_build_output(deployment):
            count += 1

    return count


def create_planned(
    device_type: FleetDeviceType,
    site=None,
    target_date: date | None = None,
    deployment_type: str = Deployment.DeploymentType.NEW_STATION,
    replaces: Deployment | None = None,
    **kwargs,
) -> Deployment:
    """Plan a deployment by hand, before any build order exists.

    The coverage is copied from the site unless given.
    """
    if 'coverage' not in kwargs and site is not None:
        kwargs['coverage'] = site.coverage

    deployment = Deployment(
        status=DeploymentStatus.PLANNED.value,
        device_type=device_type,
        site=site,
        target_date=target_date,
        deployment_type=deployment_type,
        replaces=replaces,
        **kwargs,
    )

    deployment.full_clean()
    deployment.save()

    return deployment


@transaction.atomic
def create_build_for(deployment: Deployment, user=None):
    """Create the build order (quantity 1) which will produce the device.

    The build order is left PENDING. Issuing it moves the deployment to
    IN_PRODUCTION (through the build hook).
    """
    from build.models import Build

    if deployment.status != DeploymentStatus.PLANNED.value:
        raise ValidationError(
            _('A build order can only be created for a planned deployment')
        )

    if deployment.build is not None:
        raise ValidationError(_('This deployment already has a build order'))

    if deployment.device is not None:
        raise ValidationError(_('This deployment already has a device'))

    target_date = None

    if deployment.target_date:
        target_date = deployment.target_date - timedelta(
            days=get_global_setting('FLEET_READY_LEAD_DAYS', cache=False)
        )

    title = str(_('Fleet deployment')) + f' {deployment.reference}'

    if deployment.site:
        title += f' ({deployment.site.name})'

    build = Build.objects.create(
        part=deployment.device_type.part,
        quantity=1,
        title=title[:100],
        target_date=target_date,
        issued_by=user,
    )

    deployment.build = build
    deployment.save()

    return build


def assign_existing_device(deployment: Deployment, stock_item, user=None):
    """Assign an existing (e.g. refurbished) unit to a deployment.

    The unit must be serialized and in stock, of the device type part or one of
    its variants, and not already part of another open deployment.
    """
    allowed = [
        DeploymentStatus.PLANNED.value,
        DeploymentStatus.IN_PRODUCTION.value,
        DeploymentStatus.READY.value,
        DeploymentStatus.SCHEDULED.value,
    ]

    if deployment.status not in allowed:
        raise ValidationError(
            _('A device can only be assigned to a pipeline deployment')
        )

    if (
        deployment.build is not None
        and deployment.build.status in BuildStatusGroups.ACTIVE_CODES
    ):
        raise ValidationError(
            _(
                'This deployment is linked to an open build order, which will produce the device'
            )
        )

    if not stock_item.serialized:
        raise ValidationError({'stock_item': _('The device must be serialized')})

    if not stock_item.in_stock or stock_item.is_building:
        raise ValidationError({'stock_item': _('The device must be in stock')})

    if is_decommissioned(stock_item):
        raise ValidationError({
            'stock_item': _('This device is decommissioned. Clear its state first')
        })

    valid_parts = deployment.device_type.part.get_descendants(include_self=True)

    if stock_item.part not in valid_parts:
        raise ValidationError({
            'stock_item': _(
                'The device must be of the device type part, or a variant of it'
            )
        })

    other = (
        Deployment.objects
        .filter(device=stock_item)
        .filter(
            status__in=DeploymentStatusGroups.PIPELINE + DeploymentStatusGroups.ACTIVE
        )
        .exclude(pk=deployment.pk)
        .first()
    )

    if other is not None:
        raise ValidationError({
            'stock_item': _('The device is already part of deployment')
            + f' {other.reference}'
        })

    previous = deployment.device
    deployment.device = stock_item

    if deployment.status != DeploymentStatus.SCHEDULED.value:
        deployment.status = DeploymentStatus.READY.value

    deployment.save()

    ensure_device_link(stock_item)

    # A scheduled deployment: its deployment task (and trip kit line) follow
    if previous is not None and previous != stock_item:
        from fleet.models import MaintenanceTask, TaskType, TripKitLine
        from fleet.status_codes import TaskStatus

        tasks = deployment.tasks.filter(
            status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value],
            task_type__in=[TaskType.DEPLOYMENT, TaskType.SWAP],
        )

        TripKitLine.objects.filter(
            task__in=tasks, source=TripKitLine.Source.DEVICE
        ).update(stock_item=stock_item, part=stock_item.part)
        MaintenanceTask.objects.filter(pk__in=tasks.values('pk')).update(
            device=stock_item
        )

    return deployment


def get_parts_short(build) -> list[dict]:
    """Return the build lines which cannot be covered by the available stock.

    Uses the same annotations as the build line API (allocated, available_stock).
    """
    from build.serializers import BuildLineSerializer

    lines = BuildLineSerializer.annotate_queryset(build.build_lines.all(), build=build)

    short = []

    for line in lines:
        required = (
            Decimal(line.quantity)
            - Decimal(line.allocated or 0)
            - Decimal(line.consumed or 0)
        )
        available = Decimal(line.available_stock or 0)

        if required > available:
            short.append({
                'part': line.part.pk,
                'name': line.part.full_name,
                'short': float(required - available),
            })

    return short


def readiness_risks(deployment: Deployment, today: date | None = None) -> list[dict]:
    """Return the readiness risks of a pipeline deployment (computed, not stored).

    Each risk is a dict of {code, severity, message}. The codes match the
    alert types: BUILD_LATE, NOT_READY, PARTS_SHORT and NO_DEPLOY_DATE.
    """
    if deployment.status not in DeploymentStatusGroups.PIPELINE:
        return []

    today = today or InvenTree.helpers.current_date()
    build = deployment.build
    build_open = build is not None and build.status in BuildStatusGroups.ACTIVE_CODES
    risks = []

    if (
        build_open
        and build.target_date
        and deployment.target_date
        and build.target_date > deployment.target_date
    ):
        risks.append({
            'code': Alert.AlertType.BUILD_LATE.value,
            'severity': AlertSeverity.WARNING.value,
            'message': str(_('Build order target date is after the deployment date')),
        })

    if (
        deployment.target_date
        and deployment.status < DeploymentStatus.READY.value
        and (deployment.target_date - today).days
        <= get_global_setting('FLEET_READY_LEAD_DAYS', cache=False)
    ):
        risks.append({
            'code': Alert.AlertType.NOT_READY.value,
            'severity': AlertSeverity.CRITICAL.value
            if deployment.target_date < today
            else AlertSeverity.WARNING.value,
            'message': str(_('Device is not ready for the deployment date')),
        })

    if build_open and (short := get_parts_short(build)):
        names = ', '.join(line['name'] for line in short[:5])
        risks.append({
            'code': Alert.AlertType.PARTS_SHORT.value,
            'severity': AlertSeverity.WARNING.value,
            'message': str(_('Not enough stock for the build order')) + f': {names}',
        })

    if deployment.target_date is None and deployment.creation_date:
        age = (today - deployment.creation_date).days

        if age > get_global_setting('FLEET_UNSCHEDULED_REMINDER_DAYS', cache=False):
            risks.append({
                'code': Alert.AlertType.NO_DEPLOY_DATE.value,
                'severity': AlertSeverity.INFO.value,
                'message': str(_('No deployment date has been set')),
            })

    return risks
