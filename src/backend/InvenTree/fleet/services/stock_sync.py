"""Stock-driven deployments: the stock list decides which devices are deployed.

A serialized stock item of an active fleet device type part (or a variant of
it) which has a customer is a deployed device (the legacy "deployed = has
customer" convention). The fleet app follows the stock automatically:

- open: a deployed device without a DEPLOYED deployment gets one (its pipeline
  deployment is reused when it has one)
- close: a DEPLOYED deployment whose device no longer has a customer (returned,
  or the item was deleted) becomes RECOVERED
- update: the client of a DEPLOYED deployment follows the customer

A DECOMMISSIONED device (internal state, section 2.11) is never opened while
the state is set, even with a customer, and a DEPLOYED deployment of it is
closed.

No stock tracking entries are written: the stock change is already recorded.
Customers are often changed by bulk operations or sales order shipping (no
post_save), so this runs from the scheduled pipeline sync task.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import structlog

from fleet.events import trigger_status_event
from fleet.models import Deployment, FleetDeviceType
from fleet.services.deployment import compute_next_pm, copy_streams, resolve_open_alerts
from fleet.services.device_state import (
    clear_on_close,
    close_decommissioned,
    decommissioned_q,
)
from fleet.services.pipeline import ensure_device_link
from fleet.status_codes import DeploymentStatus, DeploymentStatusGroups
from stock.models import StockItem, StockItemTracking
from stock.status_codes import StockHistoryCode

logger = structlog.get_logger('inventree')

# Tracking codes which mean "sent to a customer" (same as the legacy widget)
DEPLOY_CODES = [
    StockHistoryCode.SHIPPED_AGAINST_SALES_ORDER.value,
    StockHistoryCode.SENT_TO_CUSTOMER.value,
]

# Tracking codes which mean "back from the customer" (by hand, or a return order)
RETURN_CODES = [
    StockHistoryCode.RETURNED_FROM_CUSTOMER.value,
    StockHistoryCode.RETURNED_AGAINST_RETURN_ORDER.value,
]


class DryRunError(Exception):
    """Raised to roll back a dry run."""


def last_tracking_date(item: StockItem, codes: list[int], after=None):
    """Return the date of the last tracking entry of an item with one of the codes."""
    entries = StockItemTracking.objects.filter(item=item, tracking_type__in=codes)

    if after is not None:
        entries = entries.filter(date__gte=after)

    entry = entries.order_by('-date', '-pk').first()

    return entry.date if entry else None


def device_type_map() -> dict[int, FleetDeviceType]:
    """Map each fleet part (device type parts and their variants) to its device type.

    A variant which has a device type of its own uses that one.
    """
    device_types = list(
        FleetDeviceType.objects.filter(active=True).select_related('part')
    )
    result = {}

    for device_type in device_types:
        for part_id in device_type.part.get_descendants(include_self=True).values_list(
            'pk', flat=True
        ):
            result.setdefault(part_id, device_type)

    for device_type in device_types:
        result[device_type.part_id] = device_type

    return result


def deployed_items(parts: dict[int, FleetDeviceType]):
    """Return the stock items which count as deployed fleet devices."""
    return (
        StockItem.objects
        .filter(
            part__in=list(parts.keys()),
            customer__isnull=False,
            serial__isnull=False,
            quantity=1,
            is_building=False,
        )
        .exclude(serial='')
        .select_related('customer', 'part')
        .order_by('serial_int', 'serial', 'pk')
    )


def describe(deployment: Deployment, item: StockItem | None = None) -> str:
    """Short description of a deployment for the summary."""
    item = item or deployment.device
    text = deployment.reference

    if item is not None:
        text += f': {item.part.full_name} #{item.serial}'

    if deployment.site:
        text += f' at {deployment.site.reference} "{deployment.site.name}"'

    return text


def open_deployment(item: StockItem, device_type: FleetDeviceType) -> Deployment:
    """Open the DEPLOYED deployment of a device which is with a customer.

    A pipeline deployment of the device (e.g. from its build order) is moved
    to DEPLOYED, otherwise a new deployment without a site or position is
    created.
    """
    deployed_at = last_tracking_date(item, DEPLOY_CODES) or timezone.now()

    deployment = (
        Deployment.objects
        .filter(device=item, status__in=DeploymentStatusGroups.PIPELINE)
        .order_by('-status', 'pk')
        .first()
    )

    if deployment is None:
        deployment = Deployment(
            device_type=device_type,
            device=item,
            deployment_type=Deployment.DeploymentType.NEW_STATION,
        )

    elif deployment.site is not None:
        busy = (
            Deployment.objects
            .filter(site=deployment.site, status=DeploymentStatus.DEPLOYED.value)
            .exclude(pk=deployment.pk)
            .first()
        )

        if busy is not None:
            # One active deployment per site: keep the device, drop the site
            metadata = deployment.metadata or {}
            metadata['warning'] = (
                f'Site {deployment.site.reference} still had an active deployment '
                f'({busy.reference}) when the device was sent to the customer; '
                'the site was not set'
            )
            deployment.metadata = metadata
            deployment.site = None

        elif deployment.latitude is None and deployment.longitude is None:
            deployment.latitude = deployment.site.latitude
            deployment.longitude = deployment.site.longitude

            if deployment.depth_m is None:
                deployment.depth_m = deployment.site.depth_m

    deployment.status = DeploymentStatus.DEPLOYED.value
    deployment.client = item.customer
    deployment.deployed_at = deployed_at
    deployment.recovered_at = None
    deployment.save()

    copy_streams(deployment)
    ensure_device_link(item)
    compute_next_pm(deployment)

    trigger_status_event(deployment)

    return deployment


def close_deployment(deployment: Deployment) -> Deployment:
    """Close a DEPLOYED deployment whose device is no longer with a customer."""
    recovered_at = None

    if deployment.device is not None:
        recovered_at = last_tracking_date(
            deployment.device, RETURN_CODES, after=deployment.deployed_at
        )

    recovered_at = recovered_at or timezone.now()

    deployment.status = DeploymentStatus.RECOVERED.value
    deployment.recovered_at = recovered_at
    deployment.save()

    resolve_open_alerts(deployment)
    clear_on_close(deployment.device)

    trigger_status_event(deployment)

    return deployment


def run_step(summary: dict, action, text: str):
    """Run one device change in its own savepoint; log and record errors."""
    try:
        with transaction.atomic():
            result = action()
    except Exception as exc:
        logger.exception('Fleet stock sync failed for %s', text)
        summary['errors'].append(f'{text}: {exc}')
        return None

    return result


def sync_stock_deployments(dry_run: bool = False) -> dict:
    """Make the DEPLOYED deployments follow the customer of the fleet devices.

    Arguments:
        dry_run: Work out the changes, then roll them back

    Returns:
        A summary dict with the lists 'opened', 'closed', 'updated' and
        'errors' (one line of text per device)
    """
    summary = {'opened': [], 'closed': [], 'updated': [], 'errors': []}

    try:
        with transaction.atomic():
            sync_changes(summary)

            if dry_run:
                raise DryRunError
    except DryRunError:
        pass

    return summary


def sync_changes(summary: dict) -> None:
    """Apply the stock-driven changes (see sync_stock_deployments)."""
    parts = device_type_map()
    active = Deployment.objects.filter(status=DeploymentStatus.DEPLOYED.value)

    # 1. Close: the device was returned, or deleted, or is decommissioned
    for deployment in active.filter(
        Q(device__isnull=True) | Q(device__customer__isnull=True) | decommissioned_q()
    ).select_related('device', 'device__part', 'device__fleet_link', 'site'):
        text = describe(deployment)
        action = close_deployment

        if deployment.device is not None and deployment.device.customer_id:
            # Still with the customer: closed because it is decommissioned
            text += ' (decommissioned)'
            action = close_decommissioned

        if run_step(summary, lambda dep=deployment, act=action: act(dep), text):
            summary['closed'].append(text)

    # 2. Update: the customer changed
    for deployment in active.filter(device__customer__isnull=False).select_related(
        'device', 'device__part', 'device__customer', 'site'
    ):
        customer = deployment.device.customer

        if deployment.client_id == customer.pk:
            continue

        text = f'{describe(deployment)}: client -> {customer.name}'

        def update_client(dep=deployment, customer=customer):
            dep.client = customer
            dep.save()
            return dep

        if run_step(summary, update_client, text):
            summary['updated'].append(text)

    if not parts:
        return

    # 3. Open: a fleet device with a customer and no DEPLOYED deployment
    #    (never a decommissioned one)
    items = (
        deployed_items(parts)
        .exclude(fleet_deployments__status=DeploymentStatus.DEPLOYED.value)
        .exclude(decommissioned_q('fleet_link__state'))
    )

    for item in items:
        device_type = parts[item.part_id]
        text = f'{item.part.full_name} #{item.serial} ({item.customer.name})'

        deployment = run_step(
            summary,
            lambda item=item, device_type=device_type: open_deployment(
                item, device_type
            ),
            text,
        )

        if deployment is not None:
            summary['opened'].append(
                f'{describe(deployment, item)} (customer {item.customer.name}, '
                f'deployed {deployment.deployed_at:%Y-%m-%d})'
            )
