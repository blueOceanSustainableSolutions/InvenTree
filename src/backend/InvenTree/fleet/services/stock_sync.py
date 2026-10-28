"""Stock-driven fleet rows: every fleet unit has its row in the deployment list.

Shipping is not deploying. A device is DEPLOYED only through the fleet Deploy
action (directly, or by completing its deployment task); the customer of the
stock item does not open or close a deployment. A unit shipped to a customer
is out of stock, but still waits to be deployed.

The fleet app follows the stock automatically:

- close: a DEPLOYED deployment whose stock item was deleted, or whose device
  is DECOMMISSIONED (internal state, section 2.11), becomes RECOVERED
- update: the client of a DEPLOYED deployment follows the customer
- pool: every other finished fleet unit which is not deployed (in stock,
  shipped to a customer, or on its way back on a return order) gets a READY
  deployment without a date, so the deployment list shows all of them
- gone: a READY deployment whose unit left the pool (used elsewhere,
  destroyed, lost, decommissioned, deleted) is CANCELLED

A deployed device cannot be returned through the normal return order or
stock menus; it comes back through the fleet (see pipeline.check_return_allowed).

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
from fleet.services.deployment import resolve_open_alerts
from fleet.services.device_state import (
    clear_on_close,
    close_decommissioned,
    decommissioned_q,
)
from fleet.services.pipeline import (
    HOLDING_STATUSES,
    ensure_unit_deployment,
    undeployed_units,
)
from fleet.status_codes import DeploymentStatus
from stock.models import StockItem

logger = structlog.get_logger('inventree')


class DryRunError(Exception):
    """Raised to roll back a dry run."""


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


def describe(deployment: Deployment, item: StockItem | None = None) -> str:
    """Short description of a deployment for the summary."""
    item = item or deployment.device
    text = deployment.reference

    if item is not None:
        text += f': {item.part.full_name} #{item.serial}'

    if deployment.site:
        text += f' at {deployment.site.reference} "{deployment.site.name}"'

    return text


def close_deployment(deployment: Deployment) -> Deployment:
    """Close a DEPLOYED deployment whose stock item was deleted."""
    deployment.status = DeploymentStatus.RECOVERED.value
    deployment.recovered_at = timezone.now()
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
    """Keep the fleet rows in step with the stock (see the module docstring).

    Arguments:
        dry_run: Work out the changes, then roll them back

    Returns:
        A summary dict with the lists 'opened' (always empty: only the
        fleet Deploy action deploys a device), 'closed', 'updated',
        'available' (new rows for undeployed units), 'cancelled' (units which
        are gone) and 'errors' (one line of text per device)
    """
    summary = {
        'opened': [],
        'closed': [],
        'updated': [],
        'available': [],
        'cancelled': [],
        'errors': [],
    }

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

    # 1. Close: the stock item was deleted, or the device is decommissioned
    #    (a device with no customer stays deployed: shipping is not deploying)
    for deployment in active.filter(
        Q(device__isnull=True) | decommissioned_q()
    ).select_related('device', 'device__part', 'device__fleet_link', 'site'):
        text = describe(deployment)
        action = close_deployment

        if deployment.device is not None:
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

    # 3. Pool: an undeployed unit without an open deployment gets a READY one
    #    (an output of a fleet build order is linked by the pipeline sync)
    pool = undeployed_units(parts.keys())
    pool_ids = set(pool.values_list('pk', flat=True))

    for item in (
        pool.exclude(fleet_deployments__status__in=HOLDING_STATUSES)
        .exclude(build__fleet_deployment__status__in=HOLDING_STATUSES)
        .select_related('part')
    ):
        device_type = parts[item.part_id]
        text = f'{item.part.full_name} #{item.serial}'

        deployment = run_step(
            summary,
            lambda item=item, device_type=device_type: ensure_unit_deployment(
                item, device_type
            ),
            text,
        )

        if deployment is not None:
            summary['available'].append(describe(deployment, item))

    # 4. Gone: a READY deployment whose unit left the pool is cancelled
    for deployment in Deployment.objects.filter(
        status=DeploymentStatus.READY.value
    ).select_related('device', 'device__part', 'site'):
        if deployment.device_id in pool_ids:
            continue

        if deployment.device_id is not None and deployment.device.is_building:
            continue

        text = describe(deployment)

        if run_step(summary, lambda dep=deployment: cancel_gone(dep), text):
            summary['cancelled'].append(text)


def cancel_gone(deployment: Deployment) -> Deployment:
    """Cancel the READY deployment of a unit which is no longer a fleet unit."""
    from fleet.services import carry_over

    deployment.status = DeploymentStatus.CANCELLED.value
    deployment.save()

    carry_over.release(deployment)

    trigger_status_event(deployment)

    return deployment
