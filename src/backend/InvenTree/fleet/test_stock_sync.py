"""Tests for the stock-driven deployments (phase P2b, FLEET_PLAN.md section 2.10)."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from fleet.helpers import to_local_date
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
from part.models import Part
from stock.models import StockItem, StockItemTracking
from stock.status_codes import StockHistoryCode


class StockSyncTest(FleetServiceTestBase):
    """Devices with a customer are DEPLOYED deployments, automatically."""

    def give_to_customer(self, item, customer=None, days_ago=None):
        """Assign a stock item to a customer by hand (tracking code 100)."""
        item.allocateToCustomer(customer or self.client_company, user=self.user)
        item.refresh_from_db()

        if days_ago is not None:
            StockItemTracking.objects.filter(
                item=item, tracking_type=StockHistoryCode.SENT_TO_CUSTOMER.value
            ).update(date=timezone.now() - timedelta(days=days_ago))

        return item

    def test_open_by_hand(self):
        """A fleet device given a customer by hand gets a DEPLOYED deployment."""
        item = self.give_to_customer(self.make_device('AUR-0500'), days_ago=40)
        codes_before = self.tracking_codes(item)

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['opened']), 1)
        self.assertEqual(summary['errors'], [])

        dep = Deployment.objects.get(device=item)
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.device_type, self.device_type)
        self.assertEqual(dep.client, self.client_company)
        self.assertEqual(dep.deployment_type, Deployment.DeploymentType.NEW_STATION)
        self.assertIsNone(dep.site)
        self.assertIsNone(dep.latitude)
        self.assertIsNone(dep.longitude)

        # Deployment date from the stock history; next PM from it
        sent = StockItemTracking.objects.get(
            item=item, tracking_type=StockHistoryCode.SENT_TO_CUSTOMER.value
        ).date
        self.assertEqual(dep.deployed_at, sent)
        self.assertEqual(dep.next_pm_date, to_local_date(sent) + timedelta(days=180))

        self.assertEqual(
            set(dep.streams.values_list('key', flat=True)), {'hydrophone', 'battery'}
        )
        self.assertTrue(DeviceLink.objects.filter(stock_item=item).exists())

        # No stock tracking entries are written
        self.assertEqual(self.tracking_codes(item), codes_before)

        # Idempotent
        summary = sync_stock_deployments()
        self.assertEqual(summary['opened'], [])
        self.assertEqual(Deployment.objects.filter(device=item).count(), 1)

    def test_open_by_sales_order_shipment(self):
        """A fleet device shipped against a sales order is deployed."""
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

        # Allocated but not shipped: not deployed
        sync_stock_deployments()
        self.assertFalse(Deployment.objects.filter(device=item).exists())

        shipment.complete_shipment(None)
        item.refresh_from_db()
        self.assertEqual(item.customer, self.client_company)

        fleet_sync_pipeline()

        dep = Deployment.objects.get(device=item)
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.client_company)

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

        # In stock (no customer)
        self.make_device('AUR-0520')

        summary = sync_stock_deployments()
        self.assertEqual(summary['opened'], [])
        self.assertFalse(Deployment.objects.exists())

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

    def test_reuse_pipeline_deployment(self):
        """A READY deployment of the device is moved to DEPLOYED (no new one)."""
        dep = self.ready_deployment(serial='AUR-0530')
        self.give_to_customer(dep.device)

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['opened']), 1)
        self.assertEqual(Deployment.objects.count(), 1)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.client_company)
        self.assertEqual(dep.site, self.site)

        # The site position is used, as with deploy()
        self.site.refresh_from_db()
        self.assertEqual(dep.latitude, self.site.latitude)

    def test_reuse_pipeline_deployment_site_busy(self):
        """When the planned site is occupied, the device is deployed without it."""
        first = self.ready_deployment(serial='AUR-0540')
        deployment_service.deploy(first)

        dep = self.ready_deployment(serial='AUR-0541')
        self.give_to_customer(dep.device)

        summary = sync_stock_deployments()
        self.assertEqual(summary['errors'], [])

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertIsNone(dep.site)
        self.assertIn(first.reference, dep.metadata['warning'])

    def test_close_on_return(self):
        """Returning the device closes the deployment and resolves its alerts."""
        item = self.give_to_customer(self.make_device('AUR-0550'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

        alert = Alert.objects.create(
            deployment=dep,
            alert_type=Alert.AlertType.NO_CONTACT,
            severity=AlertSeverity.CRITICAL.value,
            message='No contact',
            dedupe_key=f'dp:{dep.pk}:NO_CONTACT',
        )

        item.return_from_customer(self.store, self.user)

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['closed']), 1)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIsNotNone(dep.recovered_at)

        returned = StockItemTracking.objects.get(
            item=item, tracking_type=StockHistoryCode.RETURNED_FROM_CUSTOMER.value
        )
        self.assertEqual(dep.recovered_at, returned.date)

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.AUTO)

        # Giving it to a customer again opens a new deployment
        self.give_to_customer(item)
        sync_stock_deployments()
        self.assertEqual(
            Deployment.objects.filter(
                device=item, status=DeploymentStatus.DEPLOYED.value
            ).count(),
            1,
        )
        self.assertEqual(Deployment.objects.filter(device=item).count(), 2)

    def test_close_on_return_order(self):
        """Receiving the device on a return order closes the deployment (history kept)."""
        from order.models import ReturnOrder, ReturnOrderLineItem

        item = self.give_to_customer(self.make_device('AUR-0555'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

        order = ReturnOrder.objects.create(customer=self.client_company)
        line = ReturnOrderLineItem.objects.create(order=order, item=item)
        order.receive_line_item(line, self.workshop, self.user)

        item.refresh_from_db()
        self.assertIsNone(item.customer)

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['closed']), 1)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)

        received = StockItemTracking.objects.get(
            item=item,
            tracking_type=StockHistoryCode.RETURNED_AGAINST_RETURN_ORDER.value,
        )
        self.assertEqual(dep.recovered_at, received.date)

        # Kept as history: listed as closed, not as active
        self.assertTrue(
            Deployment.objects.filter(
                device=item, status=DeploymentStatus.RECOVERED.value
            ).exists()
        )

    def test_close_when_item_deleted(self):
        """A deployment whose stock item was deleted is closed."""
        item = self.give_to_customer(self.make_device('AUR-0560'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

        item.delete()

        sync_stock_deployments()
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)

    def test_customer_changed(self):
        """The client follows the customer of the device."""
        item = self.give_to_customer(self.make_device('AUR-0570'))
        sync_stock_deployments()

        item.customer = self.customer
        item.save()

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['updated']), 1)

        dep = Deployment.objects.get(device=item)
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.client, self.customer)

    def test_deploy_and_recover_need_no_sync(self):
        """The fleet deploy and recover actions already match the stock."""
        dep = self.ready_deployment(serial='AUR-0580')
        deployment_service.deploy(dep)

        summary = sync_stock_deployments()
        self.assertEqual(
            summary, {'opened': [], 'closed': [], 'updated': [], 'errors': []}
        )

        deployment_service.recover(dep)

        summary = sync_stock_deployments()
        self.assertEqual(
            summary, {'opened': [], 'closed': [], 'updated': [], 'errors': []}
        )

    def test_dry_run(self):
        """A dry run reports the changes but saves nothing."""
        self.give_to_customer(self.make_device('AUR-0590'))

        summary = sync_stock_deployments(dry_run=True)
        self.assertEqual(len(summary['opened']), 1)
        self.assertFalse(Deployment.objects.exists())

        out = StringIO()
        call_command('fleet_sync_stock', dry_run=True, stdout=out)
        self.assertIn('Open: ', out.getvalue())
        self.assertIn('Dry run: 1 opened', out.getvalue())
        self.assertFalse(Deployment.objects.exists())

        out = StringIO()
        call_command('fleet_sync_stock', stdout=out)
        self.assertIn('Done: 1 opened', out.getvalue())
        self.assertEqual(Deployment.objects.count(), 1)

    def test_monitoring_without_position(self):
        """A device without a position is monitored, without a geofence check."""
        from fleet.integrations.base import DeviceStatus, Position, StreamStatus

        item = self.give_to_customer(self.make_device('AUR-0600'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

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
        self.assertIsNone(dep.distance_from_nominal_m)
        self.assertFalse(
            Alert.objects.filter(
                deployment=dep, alert_type=Alert.AlertType.GEOFENCE_BREACH
            ).exists()
        )

        # Once a position is set, the geofence applies
        deployment_service.set_position(dep, latitude='37.0', longitude='-8.0')
        dep.refresh_from_db()
        monitoring.apply_status(dep, status, now=now)

        self.assertTrue(
            Alert.objects.filter(
                deployment=dep, alert_type=Alert.AlertType.GEOFENCE_BREACH
            ).exists()
        )

    # P7: internal device states (section 2.11)

    def test_decommissioned_survives_sync(self):
        """A decommissioned device keeps its customer but is never reopened."""
        item = self.give_to_customer(self.make_device('AUR-0700'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)
        codes_before = self.tracking_codes(item)

        device_state.set_state(
            dep, InternalState.DECOMMISSIONED, user=self.user, note='End of life'
        )

        dep.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIn('decommissioned', dep.metadata)

        # No stock movement: the item keeps its customer
        self.assertEqual(item.customer, self.client_company)
        self.assertEqual(self.tracking_codes(item), codes_before)

        link = DeviceLink.objects.get(stock_item=item)
        self.assertEqual(link.state, InternalState.DECOMMISSIONED)
        self.assertEqual(link.state_note, 'End of life')
        self.assertEqual(link.state_changed_by, self.user)

        # The sync neither reopens nor recreates it
        for _run in range(2):
            summary = sync_stock_deployments()
            self.assertEqual(
                summary, {'opened': [], 'closed': [], 'updated': [], 'errors': []}
            )

        self.assertEqual(Deployment.objects.filter(device=item).count(), 1)
        self.assertEqual(
            device_state.device_state(dep), InternalState.DECOMMISSIONED.value
        )

        # Clearing the state lets the sync open the device again
        device_state.set_state(dep, InternalState.NONE, user=self.user)
        summary = sync_stock_deployments()
        self.assertEqual(len(summary['opened']), 1)
        self.assertEqual(
            Deployment.objects.filter(
                device=item, status=DeploymentStatus.DEPLOYED.value
            ).count(),
            1,
        )

    def test_decommissioned_before_first_sync(self):
        """A device decommissioned before it was ever deployed is not opened."""
        item = self.give_to_customer(self.make_device('AUR-0710'))
        DeviceLink.objects.create(stock_item=item, state=InternalState.DECOMMISSIONED)

        self.assertEqual(sync_stock_deployments(dry_run=True)['opened'], [])
        self.assertEqual(sync_stock_deployments()['opened'], [])
        self.assertFalse(Deployment.objects.filter(device=item).exists())

    def test_decommissioned_elsewhere_is_closed(self):
        """A DEPLOYED deployment of a device decommissioned elsewhere (e.g. the admin) is closed."""
        item = self.give_to_customer(self.make_device('AUR-0720'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

        DeviceLink.objects.filter(stock_item=item).update(
            state=InternalState.DECOMMISSIONED
        )

        summary = sync_stock_deployments()
        self.assertEqual(len(summary['closed']), 1)
        self.assertIn('(decommissioned)', summary['closed'][0])

        dep.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(item.customer, self.client_company)
        self.assertEqual(sync_stock_deployments()['opened'], [])

    def test_return_clears_docked(self):
        """Returning a docked device closes it and clears DOCKED."""
        item = self.give_to_customer(self.make_device('AUR-0730'))
        sync_stock_deployments()
        dep = Deployment.objects.get(device=item)

        device_state.set_state(dep, InternalState.DOCKED, user=self.user)
        self.assertEqual(device_state.device_state(dep), 'DOCKED')

        # Still with the customer: the sync leaves a docked device deployed
        self.assertEqual(sync_stock_deployments()['closed'], [])

        item.return_from_customer(self.store, self.user)
        sync_stock_deployments()

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(
            DeviceLink.objects.get(stock_item=item).state, InternalState.NONE
        )
        self.assertIsNone(device_state.device_state(dep))


class SalesBuildPipelineTest(FleetServiceTestBase):
    """A sold device keeps one deployment from the BO to the customer."""

    def test_sales_build_to_deployed(self):
        """PENDING sales BO -> PLANNED; output shipped -> the same deployment DEPLOYED."""
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
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(Deployment.objects.count(), 1)
        self.assertEqual(pipeline.readiness_risks(dep), [])
