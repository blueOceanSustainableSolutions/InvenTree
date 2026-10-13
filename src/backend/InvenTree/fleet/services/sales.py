"""Shipping a fleet unit from its sales order when it is deployed.

Every deployed unit goes through a sale (blueOASIS's own stations too, on a
sales order to blueOASIS). A unit which is not shipped yet when it is deployed
is shipped from its sales order on its own: fleet moves it into a shipment of
its own and completes that shipment. Other items on a pending shipment stay
there for the office to send.

The sale of a unit is its pending allocation on an open sales order, else the
open sales order of its build order (fleet allocates the unit to the matching
line). A recovered unit needs a new sale: its old order does not count.
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils.translation import gettext_lazy as _

import InvenTree.helpers
from common.settings import get_global_setting
from fleet.models import Deployment
from order.models import (
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLineItem,
    SalesOrderShipment,
)
from order.status_codes import SalesOrderStatusGroups


@dataclass
class Sale:
    """The sales order a unit is shipped from."""

    order: SalesOrder
    allocation: SalesOrderAllocation | None = None
    line: SalesOrderLineItem | None = None


def pending_allocation(device) -> SalesOrderAllocation | None:
    """The allocation of a unit on an open sales order, not shipped yet."""
    return (
        SalesOrderAllocation.objects
        .filter(
            item=device,
            shipment__shipment_date__isnull=True,
            line__order__status__in=SalesOrderStatusGroups.OPEN,
        )
        .select_related('line__order', 'shipment')
        .order_by('pk')
        .first()
    )


def ever_shipped(device) -> bool:
    """True if a unit was shipped on a sales order before."""
    return SalesOrderAllocation.objects.filter(
        item=device, shipment__shipment_date__isnull=False
    ).exists()


def open_line(order: SalesOrder, part) -> SalesOrderLineItem | None:
    """The first line of an order for a part (or a template of it) with quantity left to ship."""
    parts = part.get_ancestors(include_self=True)

    for line in (
        order.lines
        .filter(part__in=parts, quantity__gt=F('shipped'))
        .order_by('pk')
    ):
        if line.quantity - line.shipped - line.allocated_quantity() >= 1:
            return line

    return None


def get_sale(deployment: Deployment) -> Sale:
    """The sale to ship the unit of a deployment from (it is not shipped yet).

    Raises:
        ValidationError: The unit does not belong to a sale, or its sales
            order has no line left for it
    """
    device = deployment.device

    if allocation := pending_allocation(device):
        return Sale(order=allocation.line.order, allocation=allocation)

    build = deployment.build
    order = build.sales_order if build is not None else None

    if (
        order is not None
        and order.status in SalesOrderStatusGroups.OPEN
        and not ever_shipped(device)
    ):
        line = open_line(order, device.part)

        if line is None:
            raise ValidationError(
                _('Sales order')
                + f' {order.reference} '
                + _('has no line left to ship')
                + f' {device.part.full_name}'
            )

        return Sale(order=order, line=line)

    raise ValidationError(
        _(
            'The device is not on a sales order. Allocate it to a sales order before deploying it'
        )
    )


def shipment_reference(order: SalesOrder, deployment: Deployment) -> str:
    """A shipment reference for the order, unique within it: the deployment reference."""
    reference = deployment.reference
    index = 1

    while order.shipments.filter(reference=reference).exists():
        index += 1
        reference = f'{deployment.reference}-{index}'

    return reference


def ship(sale: Sale, deployment: Deployment, user=None) -> SalesOrderShipment:
    """Ship the unit of a deployment on its own from its sales order.

    The unit is allocated to the line if needed, and moved into a shipment of
    its own (the pending one is used if it holds only this unit), which is
    then completed now (not in the background: the deployment needs the unit
    shipped).
    """
    from order.tasks import complete_sales_order_shipment

    device = deployment.device
    allocation = sale.allocation
    shipment = allocation.shipment if allocation is not None else None

    if shipment is None or shipment.allocations.exclude(pk=allocation.pk).exists():
        shipment = SalesOrderShipment.objects.create(
            order=sale.order,
            reference=shipment_reference(sale.order, deployment),
            notes=str(_('Shipped by fleet deployment')) + f' {deployment.reference}',
        )

    if allocation is None:
        allocation = SalesOrderAllocation(
            line=sale.line, item=device, quantity=1, shipment=shipment
        )
        allocation.clean()
        allocation.save()

    elif allocation.shipment_id != shipment.pk:
        allocation.shipment = shipment
        allocation.save()

    if user is not None and get_global_setting('SALESORDER_SHIPMENT_REQUIRES_CHECK'):
        shipment.checked_by = user
        shipment.save()

    complete_sales_order_shipment(
        shipment.pk, user.pk if user else None, InvenTree.helpers.current_date()
    )

    device.refresh_from_db()
    shipment.refresh_from_db()

    return shipment
