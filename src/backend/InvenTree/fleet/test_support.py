"""Helpers shared by the fleet tests (no tests here)."""

from fleet.services import pipeline


def default_customer(customer=None):
    """The customer of a test sale: the default fleet customer (blueOASIS sells
    its own stations to itself), else a "Fleet test customer" company."""
    from company.models import Company
    from fleet.helpers import get_model_setting

    customer = customer or get_model_setting('FLEET_DEFAULT_CUSTOMER')

    if customer is None:
        customer, _created = Company.objects.get_or_create(
            name='Fleet test customer', defaults={'is_customer': True}
        )

    return customer


def sales_order(part, customer=None, quantity=1):
    """Create a sales order with one line for a part."""
    from order.models import SalesOrder, SalesOrderLineItem

    order = SalesOrder.objects.create(customer=default_customer(customer))
    SalesOrderLineItem.objects.create(order=order, part=part, quantity=quantity)

    return order


def new_site(name, **kwargs):
    """Create a site (without a location unless given): deploying needs a site."""
    from fleet.models import Site

    return Site.objects.create(name=name, **kwargs)


def sell_unit(unit, customer=None, shipment=None):
    """Sell a unit: allocate it to a new sales order line (not shipped).

    Returns the allocation.
    """
    from order.models import SalesOrderAllocation

    line = sales_order(unit.part, customer).lines.first()

    return SalesOrderAllocation.objects.create(
        line=line, item=unit, quantity=1, shipment=shipment
    )


def plan_unit(unit, device_type, site=None, target_date=None, **kwargs):
    """Plan a deployment for a unit as the app does it.

    The unit gets its row in the deployment list (the stock sync), and "New
    deployment" sets its site, date and type. Other keyword arguments are set
    on the deployment as they are (e.g. client).

    A unit in stock is sold first (allocated to a sales order of the client,
    else of the site client), as only sold units are deployed; pass
    sold=False to keep it unsold.
    """
    sold = kwargs.pop('sold', True)

    if sold and not unit.customer and not unit.sales_order_allocations.exists():
        customer = kwargs.get('client') or (site.client if site else None)
        sell_unit(unit, customer)

    deployment = pipeline.ensure_unit_deployment(unit, device_type)

    deployment = pipeline.new_deployment(
        deployment,
        site=site,
        target_date=target_date,
        deployment_type=kwargs.pop('deployment_type', None),
        replaces=kwargs.pop('replaces', None),
        coverage=kwargs.pop('coverage', None),
    )

    if kwargs:
        for key, value in kwargs.items():
            setattr(deployment, key, value)

        deployment.save()

    return deployment
