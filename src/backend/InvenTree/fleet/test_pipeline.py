"""Tests for the fleet pipeline and deployment services (phase P1).

Covers the acceptance scenarios 1 (pipeline from BO issue), 2 (cancel BO) and
3 (deploy, without a field trip) from FLEET_PLAN.md section 6.
"""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.utils import timezone

from build.models import Build
from build.status_codes import BuildStatus
from common.settings import set_global_setting
from company.models import Company
from fleet.helpers import to_local_date
from fleet.models import (
    Alert,
    Coverage,
    Deployment,
    DeviceLink,
    FleetDeviceType,
    MaintenanceTask,
    Site,
    StreamTemplate,
)
from fleet.services import deployment as deployment_service
from fleet.services import pipeline, planning
from fleet.status_codes import AlertSeverity, AlertStatus, DeploymentStatus, TaskStatus
from fleet.tasks import fleet_sync_pipeline
from InvenTree.helpers import current_date
from InvenTree.unit_test import InvenTreeTestCase
from part.models import BomItem, Part
from stock.models import StockItem, StockItemTracking, StockLocation
from stock.status_codes import StockHistoryCode


class FleetServiceTestBase(InvenTreeTestCase):
    """Common data: a fleet device type, a site, a customer and a workshop."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared test data."""
        super().setUpTestData()

        cls.part = Part.objects.create(
            name='Aurora PI5 75m', assembly=True, trackable=True
        )
        cls.device_type = FleetDeviceType.objects.create(part=cls.part)

        StreamTemplate.objects.create(
            device_type=cls.device_type,
            key='hydrophone',
            name='Hydrophone',
            essential=True,
            expected_interval_minutes=10,
        )
        StreamTemplate.objects.create(
            device_type=cls.device_type, key='battery', name='Battery'
        )

        cls.other_part = Part.objects.create(
            name='Office printer', assembly=True, trackable=True
        )

        cls.customer = Company.objects.create(name='BlueOasis Fleet', is_customer=True)
        cls.client_company = Company.objects.create(
            name='Port Authority', is_customer=True
        )

        cls.workshop = StockLocation.objects.create(name='Workshop / To inspect')
        cls.store = StockLocation.objects.create(name='Store')

        cls.site = Site.objects.create(
            name='Algarve #1',
            latitude='37.012345',
            longitude='-7.934567',
            depth_m=18,
            geofence_radius_m=150,
        )

    def setUp(self):
        """Configure the fleet settings used by the services."""
        super().setUp()

        set_global_setting('FLEET_DEFAULT_CUSTOMER', self.customer.pk)
        set_global_setting('FLEET_WORKSHOP_LOCATION', self.workshop.pk)

    def make_build(self, part=None, quantity=1, **kwargs):
        """Create a (PENDING) build order."""
        return Build.objects.create(
            part=part or self.part, quantity=quantity, title='Aurora', **kwargs
        )

    def make_device(self, serial):
        """Create a serialized unit of the fleet part, in stock."""
        return StockItem.objects.create(
            part=self.part, quantity=1, serial=serial, location=self.store
        )

    def ready_deployment(self, serial='AUR-0200', site=None):
        """Return a READY deployment with an existing unit assigned."""
        dep = pipeline.create_planned(self.device_type, site=site or self.site)
        pipeline.assign_existing_device(dep, self.make_device(serial))
        return dep

    def tracking_codes(self, item):
        """Return the tracking codes of a stock item, oldest first."""
        return list(
            StockItemTracking.objects
            .filter(item=item)
            .order_by('pk')
            .values_list('tracking_type', flat=True)
        )


class PipelineTest(FleetServiceTestBase):
    """Scenario 1 and 2, and the pipeline rules."""

    def test_scenario_1_pipeline_from_bo_issue(self):
        """Issue the BO, link the output, complete the build: READY."""
        build = self.make_build()

        # Creating the BO does not create a deployment
        self.assertFalse(Deployment.objects.exists())

        # Issuing it does
        build.issue_build()
        build.refresh_from_db()
        self.assertEqual(build.status, BuildStatus.PRODUCTION.value)

        dep = Deployment.objects.get(build=build)
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)
        self.assertEqual(dep.device_type, self.device_type)
        self.assertEqual(dep.deployment_type, Deployment.DeploymentType.NEW_STATION)
        self.assertIsNone(dep.site)
        self.assertIsNone(dep.target_date)
        self.assertIsNone(dep.device)

        # Saving the build again does not create a second deployment
        build.save()
        self.assertEqual(Deployment.objects.count(), 1)

        # The manager sets the site and the target date
        dep.site = self.site
        dep.target_date = current_date() + timedelta(days=60)
        dep.save()

        # A build target date after the deployment date is a risk
        build.target_date = dep.target_date + timedelta(days=5)
        build.save()
        dep.refresh_from_db()

        codes = [risk['code'] for risk in pipeline.readiness_risks(dep)]
        self.assertIn('BUILD_LATE', codes)
        self.assertNotIn('NOT_READY', codes)

        # The daily planning raises a BUILD_LATE alert, and notifies Teams once
        set_global_setting(
            'FLEET_TEAMS_WEBHOOK_URL', 'https://example.webhook.office.com/fleet'
        )

        with (
            mock.patch('fleet.notify.requests.post') as teams,
            mock.patch('fleet.notify.on_commit', side_effect=lambda func: func()),
        ):
            planning.pipeline_alerts()
            planning.pipeline_alerts()

        alert = Alert.objects.get(deployment=dep, alert_type=Alert.AlertType.BUILD_LATE)
        self.assertEqual(alert.status, AlertStatus.OPEN.value)
        self.assertEqual(teams.call_count, 1)

        card = teams.call_args.kwargs['json']['attachments'][0]['content']
        self.assertIn('Build late', card['body'][0]['text'])
        self.assertIn('Algarve #1', card['body'][0]['text'])

        # The output is created with a serial; the sync links the device
        output = build.create_build_output(1, serials=['AUR-0161']).first()
        self.assertTrue(output.is_building)

        fleet_sync_pipeline()

        dep.refresh_from_db()
        self.assertEqual(dep.device, output)
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)

        link = DeviceLink.objects.get(stock_item=output)
        self.assertEqual(link.platform_id, '')

        # Completing the build makes the deployment READY
        build.complete_build_output(output, None)
        build.complete_build(None)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.READY.value)

        # No more pipeline risks from the (closed) build order
        self.assertNotIn(
            'BUILD_LATE', [risk['code'] for risk in pipeline.readiness_risks(dep)]
        )

    def test_scenario_2_cancel_bo(self):
        """Cancelling the BO cancels the deployment."""
        build = self.make_build()
        build.issue_build()

        dep = Deployment.objects.get(build=build)
        build.cancel_build(None)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.CANCELLED.value)

        # A planned deployment whose (pending) BO is cancelled is cancelled too
        planned = pipeline.create_planned(self.device_type, site=self.site)
        other = pipeline.create_build_for(planned)
        other.cancel_build(None)

        planned.refresh_from_db()
        self.assertEqual(planned.status, DeploymentStatus.CANCELLED.value)

    def test_fleet_error_does_not_block_build(self):
        """A fleet error in the build hook is logged; the build action still succeeds."""
        build = self.make_build()

        def broken_hook(instance):
            # A partial fleet write, then a failure: the savepoint undoes the write
            pipeline.create_build_deployment(
                instance, DeploymentStatus.IN_PRODUCTION.value
            )
            raise RuntimeError('fleet bug')

        with (
            mock.patch(
                'fleet.services.pipeline.on_build_saved', side_effect=broken_hook
            ),
            mock.patch('fleet.signals.logger') as logger,
        ):
            build.issue_build()

        self.assertTrue(logger.exception.called)

        build.refresh_from_db()
        self.assertEqual(build.status, BuildStatus.PRODUCTION.value)
        self.assertFalse(Deployment.objects.exists())

        # The pipeline task catches the missed deployment up
        fleet_sync_pipeline()

        dep = Deployment.objects.get(build=build)
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)

        # A missed cancel is caught up too
        with mock.patch(
            'fleet.services.pipeline.on_build_saved',
            side_effect=RuntimeError('fleet bug'),
        ):
            build.cancel_build(None)

        build.refresh_from_db()
        self.assertEqual(build.status, BuildStatus.CANCELLED.value)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)

        fleet_sync_pipeline()

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.CANCELLED.value)

    def test_output_created_without_issue(self):
        """create_build_output also moves PENDING to PRODUCTION: still one deployment."""
        build = self.make_build()
        output = build.create_build_output(1, serials=['AUR-0170']).first()

        build.refresh_from_db()
        self.assertEqual(build.status, BuildStatus.PRODUCTION.value)

        dep = Deployment.objects.get(build=build)
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)

        self.assertTrue(pipeline.sync_build_output(dep))
        self.assertEqual(dep.device, output)

        # Nothing changes on a second sync
        self.assertFalse(pipeline.sync_build_output(dep))

    def test_not_a_fleet_part(self):
        """Builds of other parts, or of inactive device types, are ignored."""
        build = self.make_build(part=self.other_part)
        build.issue_build()
        self.assertFalse(Deployment.objects.exists())

        self.device_type.active = False
        self.device_type.save()

        build = self.make_build()
        build.issue_build()
        self.assertFalse(Deployment.objects.exists())

    def test_quantity_warning(self):
        """A build quantity other than 1 still creates one deployment, with a warning."""
        build = self.make_build(quantity=2)
        build.issue_build()

        dep = Deployment.objects.get(build=build)
        self.assertIn('quantity', dep.metadata['warning'])

    def test_planned_then_build(self):
        """A planned deployment gets a build order; issuing it starts production."""
        target = current_date() + timedelta(days=90)
        dep = pipeline.create_planned(
            self.device_type, site=self.site, target_date=target
        )

        self.assertEqual(dep.status, DeploymentStatus.PLANNED.value)
        self.assertEqual(dep.coverage, self.site.coverage)

        build = pipeline.create_build_for(dep, user=self.user)

        self.assertEqual(build.status, BuildStatus.PENDING.value)
        self.assertEqual(build.quantity, 1)
        self.assertEqual(build.part, self.part)
        self.assertEqual(build.target_date, target - timedelta(days=7))

        dep.refresh_from_db()
        self.assertEqual(dep.build, build)
        self.assertEqual(dep.status, DeploymentStatus.PLANNED.value)

        # A second build order cannot be created
        with self.assertRaises(ValidationError):
            pipeline.create_build_for(dep)

        build.issue_build()

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)
        self.assertEqual(Deployment.objects.count(), 1)

    def test_assign_existing_device(self):
        """A refurbished unit in stock can be assigned to a pipeline deployment."""
        dep = pipeline.create_planned(self.device_type, site=self.site)
        unit = self.make_device('AUR-0099')

        # Wrong part
        printer = StockItem.objects.create(
            part=self.other_part, quantity=1, serial='P1'
        )

        with self.assertRaises(ValidationError):
            pipeline.assign_existing_device(dep, printer)

        # Not serialized
        bulk = StockItem.objects.create(part=self.part, quantity=3)

        with self.assertRaises(ValidationError):
            pipeline.assign_existing_device(dep, bulk)

        pipeline.assign_existing_device(dep, unit)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.READY.value)
        self.assertEqual(dep.device, unit)
        self.assertTrue(DeviceLink.objects.filter(stock_item=unit).exists())

        # The same unit cannot be part of another open deployment
        other = pipeline.create_planned(self.device_type)

        with self.assertRaises(ValidationError):
            pipeline.assign_existing_device(other, unit)

        # A deployment with an open build order gets its device from the build
        build = self.make_build()
        build.issue_build()

        with self.assertRaises(ValidationError):
            pipeline.assign_existing_device(
                build.fleet_deployment, self.make_device('AUR-0098')
            )

    def test_readiness_risks(self):
        """NOT_READY, NO_DEPLOY_DATE and PARTS_SHORT risks."""
        today = current_date()

        # Close to the target date, and not READY
        dep = pipeline.create_planned(
            self.device_type, site=self.site, target_date=today + timedelta(days=3)
        )
        risks = {risk['code']: risk for risk in pipeline.readiness_risks(dep)}
        self.assertEqual(risks['NOT_READY']['severity'], AlertSeverity.WARNING.value)

        # Past the target date: critical
        dep.target_date = today - timedelta(days=1)
        risks = {risk['code']: risk for risk in pipeline.readiness_risks(dep)}
        self.assertEqual(risks['NOT_READY']['severity'], AlertSeverity.CRITICAL.value)

        # No date for longer than the reminder period
        dep.target_date = None
        dep.save()
        self.assertEqual(pipeline.readiness_risks(dep), [])

        Deployment.objects.filter(pk=dep.pk).update(
            creation_date=today - timedelta(days=8)
        )
        dep.refresh_from_db()
        self.assertEqual(
            [risk['code'] for risk in pipeline.readiness_risks(dep)], ['NO_DEPLOY_DATE']
        )

        # Not enough component stock for the build order
        assembly = Part.objects.create(
            name='Aurora multichannel', assembly=True, trackable=True
        )
        cable = Part.objects.create(name='Hydrophone cable', component=True)
        BomItem.objects.create(part=assembly, sub_part=cable, quantity=2)
        device_type = FleetDeviceType.objects.create(part=assembly)

        build = self.make_build(part=assembly)
        build.issue_build()

        dep = build.fleet_deployment
        dep.target_date = today + timedelta(days=60)
        dep.save()

        risks = {risk['code']: risk for risk in pipeline.readiness_risks(dep)}
        self.assertIn('Hydrophone cable', risks['PARTS_SHORT']['message'])
        self.assertEqual(dep.device_type, device_type)

        StockItem.objects.create(part=cable, quantity=5, location=self.store)
        self.assertEqual(pipeline.readiness_risks(dep), [])

        # Deployed devices have no pipeline risks
        dep.status = DeploymentStatus.DEPLOYED.value
        self.assertEqual(pipeline.readiness_risks(dep), [])


class DeploymentServiceTest(FleetServiceTestBase):
    """Scenario 3 (deploy without a trip), recovery and the next PM date."""

    def test_scenario_3_deploy(self):
        """Deploy a READY device: customer, tracking 100 + 130, streams, next PM."""
        dep = self.ready_deployment()
        device = dep.device

        deployed_at = timezone.now() - timedelta(hours=2)

        deployment_service.deploy(
            dep,
            user=self.user,
            latitude='37.012400',
            longitude='-7.934600',
            depth_m=19,
            deployed_at=deployed_at,
        )

        dep.refresh_from_db()
        device.refresh_from_db()

        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(dep.deployed_at, deployed_at)
        self.assertEqual(str(dep.latitude), '37.012400')
        self.assertEqual(dep.depth_m, 19)

        # Geofence copied from the site
        self.assertEqual(dep.geofence_radius_m, 150)

        # The site has no client, so the default fleet customer is used
        self.assertEqual(device.customer, self.customer)
        self.assertIsNone(device.location)
        self.assertFalse(device.in_stock)

        codes = self.tracking_codes(device)
        self.assertIn(StockHistoryCode.SENT_TO_CUSTOMER.value, codes)
        self.assertEqual(codes[-1], StockHistoryCode.FLEET_DEPLOYED.value)

        entry = StockItemTracking.objects.filter(
            item=device, tracking_type=StockHistoryCode.FLEET_DEPLOYED.value
        ).get()
        self.assertEqual(entry.deltas, {'deployment': dep.pk, 'site': self.site.pk})

        # Streams copied from the templates
        streams = {stream.key: stream for stream in dep.streams.all()}
        self.assertEqual(set(streams), {'hydrophone', 'battery'})
        self.assertTrue(streams['hydrophone'].essential)
        self.assertEqual(streams['hydrophone'].expected_interval_minutes, 10)

        # Next PM: deployment date + 180 days (default)
        self.assertEqual(
            dep.next_pm_date, to_local_date(deployed_at) + timedelta(days=180)
        )

        # Cannot deploy twice
        with self.assertRaises(ValidationError):
            deployment_service.deploy(dep, user=self.user)

    def test_deploy_uses_site_client(self):
        """The site client is preferred to the default fleet customer."""
        self.site.client = self.client_company
        self.site.save()

        dep = self.ready_deployment()
        deployment_service.deploy(dep, user=self.user)

        dep.device.refresh_from_db()
        self.assertEqual(dep.device.customer, self.client_company)

        # The position defaults to the site position
        dep.refresh_from_db()
        self.site.refresh_from_db()
        self.assertEqual(dep.latitude, self.site.latitude)
        self.assertEqual(dep.depth_m, self.site.depth_m)

    def test_deploy_validation(self):
        """Deployments must be ready, have a device and a customer."""
        dep = pipeline.create_planned(self.device_type, site=self.site)

        with self.assertRaises(ValidationError):
            deployment_service.deploy(dep)

        dep = self.ready_deployment(serial='AUR-0300')

        set_global_setting('FLEET_DEFAULT_CUSTOMER', '')

        with self.assertRaises(ValidationError):
            deployment_service.deploy(dep)

        set_global_setting('FLEET_DEFAULT_CUSTOMER', self.customer.pk)
        deployment_service.deploy(dep)

        # The site is now occupied
        other = self.ready_deployment(serial='AUR-0301')

        with self.assertRaises(ValidationError):
            deployment_service.deploy(other)

        other.refresh_from_db()
        self.assertEqual(other.status, DeploymentStatus.READY.value)

    def test_deploy_without_site(self):
        """A site is optional: the position comes from the arguments, or stays empty."""
        dep = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(dep, self.make_device('AUR-0400'))
        deployment_service.deploy(dep, user=self.user)

        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertIsNone(dep.site)
        self.assertIsNone(dep.latitude)
        self.assertIsNone(dep.longitude)
        self.assertIsNone(dep.geofence_radius_m)
        self.assertEqual(dep.client, self.customer)
        self.assertEqual(dep.device.customer, self.customer)

        entry = StockItemTracking.objects.filter(
            item=dep.device, tracking_type=StockHistoryCode.FLEET_DEPLOYED.value
        ).get()
        self.assertEqual(entry.deltas, {'deployment': dep.pk, 'site': None})

        # With a position given, it is used
        other = pipeline.create_planned(self.device_type, client=self.client_company)
        pipeline.assign_existing_device(other, self.make_device('AUR-0401'))
        deployment_service.deploy(
            other, latitude='38.500000', longitude='-9.100000', depth_m=12
        )

        other.refresh_from_db()
        self.assertEqual(str(other.latitude), '38.500000')
        self.assertEqual(other.depth_m, 12)
        self.assertEqual(other.device.customer, self.client_company)

        # Two deployments without a site do not collide
        self.assertEqual(
            Deployment.objects.filter(
                status=DeploymentStatus.DEPLOYED.value, site__isnull=True
            ).count(),
            2,
        )

    def test_set_position(self):
        """Set the position of a deployed device; closed deployments are refused."""
        dep = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(dep, self.make_device('AUR-0410'))
        deployment_service.deploy(dep)

        deployment_service.set_position(
            dep, latitude='37.1', longitude='-8.2', depth_m=5, geofence_radius_m=300
        )
        dep.refresh_from_db()
        self.assertEqual(str(dep.latitude), '37.100000')
        self.assertEqual(dep.geofence_radius_m, 300)
        self.assertEqual(dep.depth_m, 5)

        # The radius and depth are kept when not given
        deployment_service.set_position(dep, latitude='37.2', longitude='-8.3')
        dep.refresh_from_db()
        self.assertEqual(dep.geofence_radius_m, 300)
        self.assertEqual(str(dep.longitude), '-8.300000')

        with self.assertRaises(ValidationError):
            deployment_service.set_position(dep, latitude='95', longitude='0')

        deployment_service.recover(dep)

        with self.assertRaises(ValidationError):
            deployment_service.set_position(dep, latitude='37', longitude='-8')

    def test_sales_build_planned_when_created(self):
        """A fleet BO linked to a sales order enters the pipeline when created."""
        from order.models import SalesOrder

        order = SalesOrder.objects.create(customer=self.client_company)

        # Without a sales order, a pending BO has no deployment
        plain = self.make_build()
        self.assertFalse(Deployment.objects.filter(build=plain).exists())

        build = self.make_build(sales_order=order)
        dep = Deployment.objects.get(build=build)
        self.assertEqual(dep.status, DeploymentStatus.PLANNED.value)
        self.assertEqual(dep.device_type, self.device_type)

        # Linking a pending BO to a sales order later also creates it
        plain.sales_order = order
        plain.save()
        self.assertTrue(Deployment.objects.filter(build=plain).exists())

        # Not for other parts
        other = self.make_build(part=self.other_part, sales_order=order)
        self.assertFalse(Deployment.objects.filter(build=other).exists())

        # Issuing the BO moves the same deployment to IN_PRODUCTION
        build.issue_build()
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.IN_PRODUCTION.value)
        self.assertEqual(Deployment.objects.filter(build=build).count(), 1)

        # Cancelling a pending sales BO cancels its deployment
        plain.refresh_from_db()
        plain.cancel_build(self.user)
        self.assertEqual(
            Deployment.objects.get(build=plain).status, DeploymentStatus.CANCELLED.value
        )

    def test_sales_build_catch_up(self):
        """The scheduled sync creates the deployment of a missed sales BO."""
        from order.models import SalesOrder

        order = SalesOrder.objects.create(customer=self.client_company)
        build = self.make_build(sales_order=order)
        Deployment.objects.filter(build=build).delete()

        fleet_sync_pipeline()
        self.assertEqual(
            Deployment.objects.get(build=build).status, DeploymentStatus.PLANNED.value
        )

        # Idempotent
        fleet_sync_pipeline()
        self.assertEqual(Deployment.objects.filter(build=build).count(), 1)

    def test_recover(self):
        """Recovering returns the device to the workshop and resolves open alerts."""
        dep = self.ready_deployment()
        deployment_service.deploy(dep, user=self.user)

        alert = Alert.objects.create(
            deployment=dep, alert_type=Alert.AlertType.NO_CONTACT, dedupe_key='x'
        )

        deployment_service.recover(dep, user=self.user, notes='End of campaign')

        dep.refresh_from_db()
        alert.refresh_from_db()
        device = dep.device
        device.refresh_from_db()

        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIsNotNone(dep.recovered_at)
        self.assertIsNone(device.customer)
        self.assertEqual(device.location, self.workshop)
        self.assertTrue(device.in_stock)

        codes = self.tracking_codes(device)
        self.assertIn(StockHistoryCode.RETURNED_FROM_CUSTOMER.value, codes)
        self.assertEqual(codes[-1], StockHistoryCode.FLEET_RECOVERED.value)

        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.AUTO)

        # Only deployed devices can be recovered
        with self.assertRaises(ValidationError):
            deployment_service.recover(dep)

    def test_recover_needs_location(self):
        """Without a workshop setting, a location must be given."""
        dep = self.ready_deployment()
        deployment_service.deploy(dep)

        set_global_setting('FLEET_WORKSHOP_LOCATION', '')

        with self.assertRaises(ValidationError):
            deployment_service.recover(dep)

        deployment_service.recover(dep, location=self.store)
        dep.device.refresh_from_db()
        self.assertEqual(dep.device.location, self.store)

    def test_replacement(self):
        """Deploying a replacement recovers the device it replaces."""
        old = self.ready_deployment(serial='AUR-0400')
        deployment_service.deploy(old)

        new = pipeline.create_planned(
            self.device_type,
            site=self.site,
            deployment_type=Deployment.DeploymentType.REPLACEMENT,
            replaces=old,
        )
        pipeline.assign_existing_device(new, self.make_device('AUR-0401'))

        deployment_service.deploy(new)

        old.refresh_from_db()
        new.refresh_from_db()

        self.assertEqual(old.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(new.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(self.site.deployments.count(), 2)

        old.device.refresh_from_db()
        self.assertIsNone(old.device.customer)
        self.assertEqual(old.device.location, self.workshop)

    def test_compute_next_pm(self):
        """Site override, last completed PM task, manual date and NO_SERVICE."""
        dep = self.ready_deployment()
        deployed_at = timezone.now() - timedelta(days=10)
        deployment_service.deploy(dep, deployed_at=deployed_at)

        deployed_on = to_local_date(deployed_at)

        self.site.pm_interval_override_days = 90
        self.site.save()
        dep.refresh_from_db()

        self.assertEqual(
            deployment_service.compute_next_pm(dep), deployed_on + timedelta(days=90)
        )

        # A completed preventive task restarts the interval
        done = timezone.now() - timedelta(days=2)
        MaintenanceTask.objects.create(
            device=dep.device,
            deployment=dep,
            task_type=MaintenanceTask.TaskType.PREVENTIVE,
            status=TaskStatus.COMPLETED.value,
            completed_at=done,
        )

        # Corrective tasks do not
        MaintenanceTask.objects.create(
            device=dep.device,
            deployment=dep,
            task_type=MaintenanceTask.TaskType.CORRECTIVE,
            status=TaskStatus.COMPLETED.value,
            completed_at=timezone.now(),
        )

        self.assertEqual(
            deployment_service.compute_next_pm(dep),
            to_local_date(done) + timedelta(days=90),
        )

        # A manual date is kept
        manual = current_date() + timedelta(days=5)
        dep.next_pm_date = manual
        dep.next_pm_manual = True
        dep.save()
        self.assertEqual(deployment_service.compute_next_pm(dep), manual)

        # No preventive maintenance for monitored-only deployments
        dep.next_pm_manual = False
        dep.coverage = Coverage.NO_SERVICE
        dep.save()
        self.assertIsNone(deployment_service.compute_next_pm(dep))
        dep.refresh_from_db()
        self.assertIsNone(dep.next_pm_date)
