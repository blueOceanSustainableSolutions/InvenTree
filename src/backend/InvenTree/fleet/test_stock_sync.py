"""Tests for the stock-driven fleet rows (FLEET_PLAN.md section 2.10).

Shipping is not deploying: only the fleet Deploy action deploys a device, and
a deployed device comes back only through the fleet.
"""

from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from fleet.models import Alert, Deployment, DeviceLink, FleetDeviceType, InternalState
from fleet.services import deployment as deployment_service
from fleet.services import device_state, monitoring, pipeline
from fleet.services.stock_sync import sync_stock_deployments
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DeploymentStatus,
    HealthStatus,
)
from fleet.tasks import fleet_sync_pipeline
from fleet.test_pipeline import FleetServiceTestBase
from fleet.test_support import new_site, plan_unit
from part.models import Part
from stock.models import StockItem
from stock.status_codes import StockHistoryCode

# A sync which changes nothing
EMPTY_SUMMARY = {
    'opened': [],
    'closed': [],
    'updated': [],
    'available': [],
    'cancelled': [],
    'errors': [],
}


class StockSyncTest(FleetServiceTestBase):
    """A customer on the stock item never deploys (or recovers) a device."""

    def give_to_customer(self, item, customer=None):
        """Assign a stock item to a customer by hand (tracking code 100)."""
        item.allocateToCustomer(customer or self.client_company, user=self.user)
        item.refresh_from_db()
        return item

    def deployed(self, serial, site=None):
        """A device deployed through the fleet (at a new site unless given)."""
        dep = plan_unit(
            self.make_device(serial),
            self.device_type,
            site=site or new_site(f'Site {serial}'),
        )
        deployment_service.deploy(dep, user=self.user)
        dep.refresh_from_db()
        dep.device.refresh_from_db()
        return dep

    def test_customer_is_not_deployed(self):
        """A fleet device given a customer by hand waits to be deployed."""
        item = self.give_to_customer(self.make_device('AUR-0500'))
        codes_before = self.tracking_codes(item)

        summary = sync_stock_deployments()
        self.assertEqual(summary['opened'], [])
        self.assertEqual(len(summary['available']), 1)
        self.assertEqual(summary['errors'], [])

        dep = Deployment.objects.get(device=item)
        self.assertEqual(dep.status, DeploymentStatus.READY.value)
        self.assertIsNone(dep.deployed_at)

        # No stock tracking entries are written; idempotent
        self.assertEqual(self.tracking_codes(item), codes_before)
        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)

        # The fleet Deploy action deploys it, with the customer it was given
        deployment_service.deploy(dep, user=self.user, site=new_site('Sines #1'))

        dep.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.client_company)
        self.assertEqual(item.customer, self.client_company)
        self.assertEqual(
            self.tracking_codes(item),
            [*codes_before, StockHistoryCode.FLEET_DEPLOYED.value],
        )
        self.assertTrue(deployment_service.deployed_by_fleet(dep))
        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)

    def test_shipped_on_sales_order_is_not_deployed(self):
        """A fleet device shipped against a sales order keeps its READY row."""
        from order.models import (
            SalesOrder,
            SalesOrderAllocation,
            SalesOrderLineItem,
            SalesOrderShipment,
        )

        self.part.salable = True
        self.part.save()

        item = self.make_device('AUR-0510')
        order = SalesOrder.objects.create(customer=self.client_company)
        line = SalesOrderLineItem.objects.create(
            order=order, part=self.part, quantity=1
        )
        shipment = SalesOrderShipment.objects.create(order=order)
        SalesOrderAllocation.objects.create(
            line=line, shipment=shipment, item=item, quantity=1
        )

        sync_stock_deployments()
        row = Deployment.objects.get(device=item)
        self.assertEqual(row.status, DeploymentStatus.READY.value)

        shipment.complete_shipment(None)
        item.refresh_from_db()
        self.assertEqual(item.customer, self.client_company)
        self.assertFalse(item.in_stock)

        fleet_sync_pipeline()

        # Shipped, not in the water: the same row, still READY
        row.refresh_from_db()
        self.assertEqual(row.status, DeploymentStatus.READY.value)
        self.assertEqual(Deployment.objects.filter(device=item).count(), 1)

        # Shipped to the default fleet customer: the same
        other = self.give_to_customer(self.make_device('AUR-0511'), self.customer)
        sync_stock_deployments()
        self.assertEqual(
            Deployment.objects.get(device=other).status, DeploymentStatus.READY.value
        )

        # It is deployed through the fleet, and keeps its customer
        pipeline.new_deployment(row, site=self.site)
        deployment_service.deploy(row)
        row.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(row.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(row.client, self.client_company)
        self.assertEqual(item.customer, self.client_company)

    def test_ignored_items(self):
        """Other parts, non-serialized items, and inactive device types are ignored."""
        StockItem.objects.create(
            part=self.other_part, quantity=1, serial='P1', customer=self.customer
        )
        StockItem.objects.create(part=self.part, quantity=5, customer=self.customer)

        inactive_part = Part.objects.create(
            name='Old buoy', assembly=True, trackable=True
        )
        FleetDeviceType.objects.create(part=inactive_part, active=False)
        StockItem.objects.create(
            part=inactive_part, quantity=1, serial='OB-1', customer=self.customer
        )

        unit = self.make_device('AUR-0520')

        summary = sync_stock_deployments()
        self.assertEqual(summary['opened'], [])
        self.assertEqual(len(summary['available']), 1)
        self.assertEqual(
            list(Deployment.objects.values_list('device', 'status')),
            [(unit.pk, DeploymentStatus.READY.value)],
        )

    def test_variant(self):
        """Variants of a device type part are fleet devices too."""
        self.part.is_template = True
        self.part.save()

        variant = Part.objects.create(
            name='Aurora PI5 75m rev B',
            assembly=True,
            trackable=True,
            variant_of=self.part,
        )
        item = StockItem.objects.create(
            part=variant, quantity=1, serial='AURB-1', customer=self.customer
        )

        sync_stock_deployments()

        dep = Deployment.objects.get(device=item)
        self.assertEqual(dep.device_type, self.device_type)
        self.assertEqual(dep.status, DeploymentStatus.READY.value)

    def test_no_customer_stays_deployed(self):
        """A deployed device whose customer is cleared stays deployed."""
        dep = self.deployed('AUR-0530')
        StockItem.objects.filter(pk=dep.device.pk).update(customer=None)

        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)

    def test_return_from_customer_refused(self):
        """A deployed device cannot be returned from the customer by hand."""
        dep = self.deployed('AUR-0550')
        item = dep.device

        with self.assertRaises(ValidationError) as err:
            item.return_from_customer(self.store, self.user)

        self.assertIn(dep.reference, str(err.exception))

        with self.assertRaises(ValidationError):
            item.return_to_stock(self.store, self.user)

        item.refresh_from_db()
        self.assertEqual(item.customer, self.customer)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)

        # The same through the API
        self.user.is_superuser = True
        self.user.save()

        api = Client()
        api.force_login(self.user)

        response = api.post(
            reverse('api-stock-return'),
            {'items': [{'pk': item.pk, 'quantity': 1}], 'location': self.store.pk},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(dep.reference, response.content.decode())

        item.refresh_from_db()
        self.assertEqual(item.customer, self.customer)

        # Recovered through the fleet, it comes back on the fleet return order
        deployment_service.recover(dep, user=self.user)
        self.assertIsNotNone(pipeline.get_return_line(item))

    def test_return_order_refused(self):
        """A deployed device cannot be put on, or received from, a return order."""
        from order.models import ReturnOrder, ReturnOrderLineItem

        dep = self.deployed('AUR-0555')
        item = dep.device

        order = ReturnOrder.objects.create(customer=self.customer)
        line = ReturnOrderLineItem(order=order, item=item, quantity=1)

        with self.assertRaises(ValidationError) as err:
            line.full_clean()

        self.assertIn('item', err.exception.message_dict)

        # A line which got there anyway (e.g. the admin) cannot be received
        line.save()

        with self.assertRaises(ValidationError):
            order.receive_line_item(line, self.workshop, self.user)

        item.refresh_from_db()
        self.assertEqual(item.customer, self.customer)
        line.delete()

        # The fleet recovery creates its own return order, which is received
        deployment_service.recover(dep, user=self.user)
        fleet_line = pipeline.get_return_line(item)
        fleet_line.full_clean()
        fleet_line.order.receive_line_item(fleet_line, self.workshop, self.user)

        item.refresh_from_db()
        self.assertIsNone(item.customer)
        self.assertEqual(item.location, self.workshop)

    def test_close_when_item_deleted(self):
        """A deployment whose stock item was deleted is closed."""
        dep = self.deployed('AUR-0560')

        dep.device.delete()

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['closed']), 1)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIsNotNone(dep.recovered_at)

    def test_customer_changed(self):
        """The client follows the customer of the device."""
        dep = self.deployed('AUR-0570')
        item = dep.device

        item.customer = self.client_company
        item.save()

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['updated']), 1)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.client_company)

    def test_deploy_and_recover_need_no_sync(self):
        """The fleet deploy and recover actions already match the stock."""
        dep = self.ready_deployment(serial='AUR-0580')
        deployment_service.deploy(dep)

        summary = sync_stock_deployments()
        self.assertEqual(summary, EMPTY_SUMMARY)

        deployment_service.recover(dep)

        summary = sync_stock_deployments()
        self.assertEqual(summary, EMPTY_SUMMARY)

    def test_dry_run(self):
        """A dry run reports the changes but saves nothing."""
        self.give_to_customer(self.make_device('AUR-0590'))

        summary = sync_stock_deployments(dry_run=True)
        self.assertEqual(len(summary['available']), 1)
        self.assertFalse(Deployment.objects.exists())

        out = StringIO()
        call_command('fleet_sync_stock', dry_run=True, stdout=out)
        self.assertIn('Available: ', out.getvalue())
        self.assertIn('1 available', out.getvalue())
        self.assertIn('Dry run', out.getvalue())
        self.assertFalse(Deployment.objects.exists())

        out = StringIO()
        call_command('fleet_sync_stock', stdout=out)
        self.assertIn('Done: 0 opened', out.getvalue())
        self.assertEqual(Deployment.objects.count(), 1)

    def test_undeploy_command(self):
        """Deployments opened by a shipment can be put back to READY."""
        by_fleet = self.deployed('AUR-0595')

        # As the old stock sync did it: DEPLOYED, without a FLEET_DEPLOYED entry
        item = self.give_to_customer(self.make_device('AUR-0596'))
        old = Deployment.objects.create(
            status=DeploymentStatus.DEPLOYED.value,
            device_type=self.device_type,
            device=item,
            client=self.client_company,
            deployed_at=timezone.now(),
        )
        deployment_service.compute_next_pm(old)
        self.assertIsNotNone(old.next_pm_date)

        alert = Alert.objects.create(
            deployment=old,
            alert_type=Alert.AlertType.NO_CONTACT,
            severity=AlertSeverity.CRITICAL.value,
            message='No contact',
            dedupe_key=f'dp:{old.pk}:NO_CONTACT',
        )
        self.assertFalse(deployment_service.deployed_by_fleet(old))

        out = StringIO()
        call_command('fleet_undeploy', stdout=out)
        self.assertIn(old.reference, out.getvalue())
        self.assertNotIn(by_fleet.reference, out.getvalue())

        out = StringIO()
        call_command('fleet_undeploy', old.reference, dry_run=True, stdout=out)
        self.assertIn('Dry run', out.getvalue())
        old.refresh_from_db()
        self.assertEqual(old.status, DeploymentStatus.DEPLOYED.value)

        call_command('fleet_undeploy', old.reference, stdout=StringIO())

        old.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(old.status, DeploymentStatus.READY.value)
        self.assertIsNone(old.deployed_at)
        self.assertIsNone(old.next_pm_date)
        self.assertEqual(item.customer, self.client_company)

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)

        # The sync keeps it as the row of the unit; the fleet can deploy it
        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)
        deployment_service.deploy(old, site=new_site('Sines #2'))
        old.refresh_from_db()
        self.assertEqual(old.status, DeploymentStatus.DEPLOYED.value)

        with self.assertRaises(CommandError):
            call_command('fleet_undeploy', 'DP-NONE', stdout=StringIO())

        # Only DEPLOYED deployments
        deployment_service.recover(by_fleet)

        with self.assertRaises(CommandError):
            call_command('fleet_undeploy', by_fleet.reference, stdout=StringIO())

    def test_monitoring_without_position(self):
        """A device at a site without a location is monitored, without a geofence check."""
        from fleet.integrations.base import DeviceStatus, Position, StreamStatus
        from fleet.services.site import move_site

        dep = self.deployed('AUR-0600')
        self.assertFalse(dep.site.has_location)

        now = timezone.now()
        status = DeviceStatus(
            platform_id='x',
            streams=[
                StreamStatus(key='hydrophone', last_seen=now),
                StreamStatus(key='battery', last_seen=now),
            ],
            position=Position(latitude=10.0, longitude=10.0, timestamp=now),
            raw={},
        )

        monitoring.apply_status(dep, status, now=now)

        dep.refresh_from_db()
        self.assertEqual(dep.health, HealthStatus.OK.value)
        self.assertIsNone(dep.distance_from_site_m)
        self.assertFalse(
            Alert.objects.filter(
                deployment=dep, alert_type=Alert.AlertType.GEOFENCE_BREACH
            ).exists()
        )

        # Once the site has a location, the geofence applies
        move_site(dep.site, self.user, Decimal('37.0'), Decimal('-8.0'), 'Surveyed')
        dep.refresh_from_db()
        monitoring.apply_status(dep, status, now=now)

        self.assertTrue(
            Alert.objects.filter(
                deployment=dep, alert_type=Alert.AlertType.GEOFENCE_BREACH
            ).exists()
        )

    # P7: internal device states (section 2.11)

    def test_decommissioned_survives_sync(self):
        """A decommissioned device keeps its customer and gets no new row."""
        dep = self.deployed('AUR-0700')
        item = dep.device
        codes_before = self.tracking_codes(item)

        device_state.set_state(
            dep, InternalState.DECOMMISSIONED, user=self.user, note='End of life'
        )

        dep.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIn('decommissioned', dep.metadata)

        # No stock movement: the item keeps its customer
        self.assertEqual(item.customer, self.customer)
        self.assertEqual(self.tracking_codes(item), codes_before)

        link = DeviceLink.objects.get(stock_item=item)
        self.assertEqual(link.state, InternalState.DECOMMISSIONED)
        self.assertEqual(link.state_note, 'End of life')
        self.assertEqual(link.state_changed_by, self.user)

        for _run in range(2):
            summary = sync_stock_deployments()
            self.assertEqual(summary, EMPTY_SUMMARY)

        self.assertEqual(Deployment.objects.filter(device=item).count(), 1)
        self.assertEqual(
            device_state.device_state(dep), InternalState.DECOMMISSIONED.value
        )

        # Clearing the state puts the unit back in the deployment list
        device_state.set_state(dep, InternalState.NONE, user=self.user)
        summary = sync_stock_deployments()
        self.assertEqual(summary['opened'], [])
        self.assertEqual(len(summary['available']), 1)
        self.assertTrue(
            Deployment.objects.filter(
                device=item, status=DeploymentStatus.READY.value
            ).exists()
        )

    def test_decommissioned_before_first_sync(self):
        """A decommissioned unit never gets a row."""
        item = self.give_to_customer(self.make_device('AUR-0710'))
        DeviceLink.objects.create(stock_item=item, state=InternalState.DECOMMISSIONED)

        self.assertEqual(sync_stock_deployments(dry_run=True), EMPTY_SUMMARY)
        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)
        self.assertFalse(Deployment.objects.filter(device=item).exists())

    def test_decommissioned_elsewhere_is_closed(self):
        """A DEPLOYED deployment of a device decommissioned elsewhere (e.g. the admin) is closed."""
        dep = self.deployed('AUR-0720')
        item = dep.device

        DeviceLink.objects.filter(stock_item=item).update(
            state=InternalState.DECOMMISSIONED
        )

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['closed']), 1)
        self.assertIn('(decommissioned)', summary['closed'][0])

        dep.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(item.customer, self.customer)
        self.assertEqual(sync_stock_deployments(), EMPTY_SUMMARY)

    def test_recover_clears_docked(self):
        """Recovering a docked device closes it and clears DOCKED."""
        dep = self.deployed('AUR-0730')
        item = dep.device

        device_state.set_state(dep, InternalState.DOCKED, user=self.user)
        self.assertEqual(device_state.device_state(dep), 'DOCKED')

        # Still deployed: the sync leaves a docked device alone
        self.assertEqual(sync_stock_deployments()['closed'], [])

        deployment_service.recover(dep, user=self.user)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(
            DeviceLink.objects.get(stock_item=item).state, InternalState.NONE
        )
        self.assertIsNone(device_state.device_state(dep))


class SalesBuildPipelineTest(FleetServiceTestBase):
    """A sold device keeps one deployment from the BO to the water."""

    def test_sales_build_to_deployed(self):
        """PENDING sales BO -> PLANNED; shipped -> still READY; Deploy -> DEPLOYED."""
        from order.models import SalesOrder

        order = SalesOrder.objects.create(customer=self.client_company)
        build = self.make_build(sales_order=order)
        dep = Deployment.objects.get(build=build)
        self.assertEqual(dep.status, DeploymentStatus.PLANNED.value)

        build.issue_build()
        build.refresh_from_db()
        output = build.create_build_output(1, serials=['AUR-0700']).first()
        build.complete_build_output(output, self.user)
        build.refresh_from_db()
        build.complete_build(self.user)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.READY.value)

        output.refresh_from_db()
        output.allocateToCustomer(self.client_company, order=order, user=self.user)

        sync_stock_deployments()

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.READY.value)
        self.assertEqual(Deployment.objects.count(), 1)

        deployment_service.deploy(dep, site=new_site('Sines #3'))
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.client_company)
        self.assertEqual(Deployment.objects.count(), 1)
        self.assertEqual(pipeline.readiness_risks(dep), [])
