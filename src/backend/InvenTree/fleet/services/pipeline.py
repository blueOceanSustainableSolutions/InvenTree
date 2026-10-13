"""Deployment pipeline: the fleet units which are not deployed.

The fleet never plans a deployment out of nothing: every row belongs to a
real unit.

- A build order for a fleet device enters the pipeline when it is issued
  (IN_PRODUCTION). One linked to a sales order enters it earlier, as PLANNED,
  as soon as it is created (still PENDING). The build output becomes the
  device, and the deployment is READY once the output is complete.
- Every other finished unit which is not deployed gets a READY deployment
  without a date (see ensure_unit_deployment, run by the stock sync). That
  includes a unit shipped to a customer: shipping is not deploying, only the
  fleet Deploy action puts a device in the water.

"New deployment" means choosing one of these units and planning its site,
date and trip (new_deployment). The plan list shows only the units which
belong to a sale (sold_q): deploying a unit ships it from its sales order.

The fleet has an outside view of the build order: it follows its status and
target date, but never creates or changes it. Manufacturing owns the build.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q
from django.utils.translation import gettext_lazy as _

import structlog

import InvenTree.helpers
import InvenTree.ready
from build.status_codes import BuildStatus
from common.settings import get_global_setting
from fleet.events import trigger_status_event
from fleet.models import Alert, Deployment, DeviceLink, FleetDeviceType
from fleet.services.device_state import check_not_decommissioned, decommissioned_q
from fleet.status_codes import AlertSeverity, DeploymentStatus, DeploymentStatusGroups
from order.status_codes import ReturnOrderStatusGroups
from stock.status_codes import StockStatus

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
            cancel_build_deployment(deployment)

    elif build.status == BuildStatus.COMPLETE.value:
        sync_build_output(deployment)

    return deployment


def cancel_build_deployment(deployment: Deployment) -> Alert:
    """Cancel the deployment of a cancelled build order, and raise an alert.

    Its deployment tasks which have not started are taken off their trips
    (PROPOSED again, kit lines removed) but kept, linked to the alert, so the
    team decides what to do with them. The BUILD_CANCELLED alert is notified
    and stays open until someone resolves it.
    """
    from fleet.services import maintenance
    from fleet.services.deployment import open_deploy_tasks
    from fleet.services.monitoring import open_or_update_alert
    from fleet.services.planning import notify_pipeline_alert

    deployment.status = DeploymentStatus.CANCELLED.value
    deployment.save()
    trigger_status_event(deployment)

    tasks = list(open_deploy_tasks(deployment).select_related('trip'))
    trips = sorted({task.trip.reference for task in tasks if task.trip is not None})

    for task in tasks:
        if task.trip is not None:
            maintenance.unschedule_task(task)

    reference = deployment.build.reference

    message = (
        str(_('Build order'))
        + f' {reference} '
        + str(_('was cancelled: deployment'))
        + f' {deployment.reference} '
        + str(_('is cancelled'))
    )

    if trips:
        message += (
            '. '
            + str(_('Its task was taken off trip'))
            + f' {", ".join(trips)} '
            + str(_('and kept open'))
        )

    alert, event = open_or_update_alert(
        f'dp:{deployment.pk}:{Alert.AlertType.BUILD_CANCELLED.value}',
        Alert.AlertType.BUILD_CANCELLED,
        AlertSeverity.WARNING.value,
        message,
        deployment=deployment,
        site=deployment.site,
        data={'build': reference, 'trips': trips},
    )

    for task in tasks:
        task.alerts.add(alert)

    if event == 'opened':
        notify_pipeline_alert(alert)

    return alert


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
        from fleet.services.deployment import attach_unit

        deployment.save()

        if deployment.status != previous_status:
            trigger_status_event(deployment)

        # Scheduled on a trip before it was built: the trip gets the device
        attach_unit(deployment)

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
        cancel_build_deployment(deployment)
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


# Stock statuses of a unit which is gone for good (not a fleet unit any more)
GONE_STOCK_STATUSES = [
    StockStatus.DESTROYED.value,
    StockStatus.LOST.value,
    StockStatus.REJECTED.value,
]

# Statuses of a deployment which holds its device (one per device)
HOLDING_STATUSES = DeploymentStatusGroups.PIPELINE + DeploymentStatusGroups.ACTIVE


def returning_q(prefix: str = '') -> Q:
    """Q for the stock items on their way back: an open return order line, not received."""
    return Q(
        **{
            f'{prefix}return_order_lines__received_date__isnull': True,
            f'{prefix}return_order_lines__order__status__in': ReturnOrderStatusGroups.OPEN,
        }
    )


def get_return_line(stock_item):
    """The open return order line of a device on its way back (None if not returning)."""
    from order.models import ReturnOrderLineItem

    if stock_item is None:
        return None

    return (
        ReturnOrderLineItem.objects.filter(
            item=stock_item,
            received_date__isnull=True,
            order__status__in=ReturnOrderStatusGroups.OPEN,
        )
        .select_related('order')
        .order_by('-pk')
        .first()
    )


def undeployed_units(part_ids):
    """The fleet units which are not deployed: the pool of the deployment list.

    A finished, serialized unit of a fleet part which is not built into
    another item, not gone (destroyed, lost, rejected), not decommissioned and
    not DEPLOYED in the fleet. It may be in stock, shipped to a customer (not
    in the water yet), or on its way back on a return order.
    """
    from stock.models import StockItem

    return (
        StockItem.objects.filter(
            part__in=list(part_ids),
            serial__isnull=False,
            quantity=1,
            is_building=False,
            belongs_to=None,
            consumed_by=None,
            consumed_by_quick_build=None,
        )
        .exclude(serial='')
        .exclude(fleet_deployments__status=DeploymentStatus.DEPLOYED.value)
        .exclude(status__in=GONE_STOCK_STATUSES)
        .exclude(decommissioned_q('fleet_link__state'))
        .distinct()
    )


def deployed_deployment(stock_item) -> Deployment | None:
    """The DEPLOYED deployment of a stock item (None if it is not deployed)."""
    if stock_item is None or stock_item.pk is None:
        return None

    return Deployment.objects.filter(
        device=stock_item, status=DeploymentStatus.DEPLOYED.value
    ).first()


def check_return_allowed(stock_item) -> None:
    """Refuse to return a device which is deployed in the fleet.

    Called by the core return paths (return order lines, return from customer,
    return to stock). A deployed device comes back only through the fleet
    (Recover, or a completed recovery task), which closes the deployment
    first and then creates its own return order.
    """
    if deployment := deployed_deployment(stock_item):
        raise ValidationError(
            _('This device is deployed in the fleet')
            + f' ({deployment.reference}). '
            + _('Recover it from the fleet maintenance to return it')
        )


def is_shipped(stock_item) -> bool:
    """True for a unit shipped to a customer which can still be deployed.

    Shipping is not deploying: the unit is out of stock, with the customer,
    but nobody has put it in the water yet.
    """
    return (
        stock_item.customer_id is not None
        and not stock_item.is_building
        and stock_item.belongs_to_id is None
        and stock_item.consumed_by_id is None
        and stock_item.status not in GONE_STOCK_STATUSES
    )


def sold_q(prefix: str = '') -> Q:
    """Q for the pipeline deployments whose unit belongs to a sale.

    Only these appear in the plan list: deploying a unit ships it from its
    sales order (see fleet.services.sales). A unit belongs to a sale when:

    - it is allocated to an open sales order, and not shipped yet
    - it has been shipped on a sales order, and is not coming back
    - its build order is for an open sales order, and it was never shipped

    A recovered device needs a new sale: its old order does not count.
    """
    from order.models import ReturnOrderLineItem, SalesOrderAllocation
    from order.status_codes import SalesOrderStatusGroups

    device = OuterRef(f'{prefix}device')

    allocated = Exists(
        SalesOrderAllocation.objects.filter(
            item=device,
            shipment__shipment_date__isnull=True,
            line__order__status__in=SalesOrderStatusGroups.OPEN,
        )
    )

    ever_shipped = Exists(
        SalesOrderAllocation.objects.filter(
            item=device, shipment__shipment_date__isnull=False
        )
    )

    returning = Exists(
        ReturnOrderLineItem.objects.filter(
            item=device,
            received_date__isnull=True,
            order__status__in=ReturnOrderStatusGroups.OPEN,
        )
    )

    shipped = (
        Q(
            **{
                f'{prefix}device__customer__isnull': False,
                f'{prefix}device__sales_order__isnull': False,
            }
        )
        & ~returning
    )

    for_sale = (
        Q(**{f'{prefix}build__sales_order__status__in': SalesOrderStatusGroups.OPEN})
        & ~ever_shipped
    )

    return allocated | shipped | for_sale


def is_sold(deployment: Deployment) -> bool:
    """True if the unit of a deployment belongs to a sale (see sold_q)."""
    return Deployment.objects.filter(pk=deployment.pk).filter(sold_q()).exists()


def ensure_unit_deployment(stock_item, device_type: FleetDeviceType | None = None):
    """Return the open deployment of a fleet unit, creating a READY one if missing.

    The fleet never plans a deployment out of nothing: each undeployed unit
    has its own row in the deployment list, without a date until someone
    plans it.
    """
    deployment = (
        Deployment.objects.filter(device=stock_item, status__in=HOLDING_STATUSES)
        .order_by('-status', 'pk')
        .first()
    )

    if deployment is not None:
        return deployment

    if device_type is None:
        from fleet.services.stock_sync import device_type_map

        device_type = device_type_map().get(stock_item.part_id)

    if device_type is None:
        raise ValidationError(_('This item is not a fleet device'))

    check_not_decommissioned(stock_item)

    deployment = Deployment.objects.create(
        status=DeploymentStatus.READY.value,
        device_type=device_type,
        device=stock_item,
        deployment_type=Deployment.DeploymentType.NEW_STATION,
    )

    ensure_device_link(stock_item)
    trigger_status_event(deployment)

    return deployment


def new_deployment(
    deployment: Deployment,
    site=None,
    target_date: date | None = None,
    deployment_type: str | None = None,
    replaces: Deployment | None = None,
    coverage: str | None = None,
) -> Deployment:
    """Plan the next deployment of an undeployed unit: site, date and type.

    "New deployment" means choosing a unit which is not deployed (built, in
    production, or on its way back) and arranging where and when it goes.
    The coverage is copied from the site unless given.
    """
    if deployment.status not in DeploymentStatusGroups.PIPELINE:
        raise ValidationError(_('Only a device which is not deployed can be planned'))

    deployment.site = site
    deployment.target_date = target_date

    if deployment_type:
        deployment.deployment_type = deployment_type

    deployment.replaces = replaces

    if coverage:
        deployment.coverage = coverage
    elif site is not None:
        deployment.coverage = site.coverage

    deployment.full_clean()
    deployment.save()

    return deployment


def readiness_risks(deployment: Deployment, today: date | None = None) -> list[dict]:
    """Return the readiness risks of a pipeline deployment (computed, not stored).

    Each risk is a dict of {code, severity, message}. The codes match the
    alert types: NOT_READY and NO_DEPLOY_DATE.

    The fleet only looks at the outcome of the build order (is the device
    ready in time?), never at how it is built: parts, stock and the build
    schedule belong to manufacturing.
    """
    if deployment.status not in DeploymentStatusGroups.PIPELINE:
        return []

    today = today or InvenTree.helpers.current_date()
    risks = []

    if (
        deployment.target_date
        and deployment.status < DeploymentStatus.READY.value
        and (deployment.target_date - today).days
        <= get_global_setting('FLEET_READY_LEAD_DAYS', cache=False)
    ):
        risks.append(
            {
                'code': Alert.AlertType.NOT_READY.value,
                'severity': AlertSeverity.CRITICAL.value
                if deployment.target_date < today
                else AlertSeverity.WARNING.value,
                'message': str(_('Device is not ready for the deployment date')),
            }
        )

    if deployment.target_date is None and deployment.creation_date:
        age = (today - deployment.creation_date).days

        if age > get_global_setting('FLEET_UNSCHEDULED_REMINDER_DAYS', cache=False):
            risks.append(
                {
                    'code': Alert.AlertType.NO_DEPLOY_DATE.value,
                    'severity': AlertSeverity.INFO.value,
                    'message': str(_('No deployment date has been set')),
                }
            )

    return risks
