"""Unit tests for the Fleet models."""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

import fleet.validators
from common.settings import set_global_setting
from fleet.models import (
    Alert,
    Deployment,
    DeviceLink,
    FaultCode,
    FieldTrip,
    FleetDeviceType,
    MaintenanceTask,
    Site,
)
from fleet.status_codes import (
    AlertStatus,
    DeploymentStatus,
    DeploymentStatusGroups,
    HealthStatus,
    TaskStatus,
)
from InvenTree.unit_test import InvenTreeAPITestCase, InvenTreeTestCase
from part.models import Part
from stock.models import StockItem
from stock.status_codes import StockHistoryCode
from users.ruleset import RuleSetEnum, get_ruleset_models


class FleetModelTest(InvenTreeTestCase):
    """Tests for the Fleet data model."""

    @classmethod
    def setUpTestData(cls):
        """Create a fleet device type with two serialized devices and two sites."""
        super().setUpTestData()

        cls.part = Part.objects.create(
            name='Aurora PI5 75m', assembly=True, trackable=True
        )
        cls.part.refresh_from_db()

        cls.device_type = FleetDeviceType.objects.create(part=cls.part)

        cls.device_a = StockItem.objects.create(
            part=cls.part, quantity=1, serial='AUR-0161'
        )
        cls.device_b = StockItem.objects.create(
            part=cls.part, quantity=1, serial='AUR-0162'
        )

        cls.site_a = Site.objects.create(
            name='Algarve #1', latitude='37.012345', longitude='-7.934567'
        )
        cls.site_b = Site.objects.create(name='Algarve #2')

    def deploy(self, device, site, **kwargs):
        """Create a DEPLOYED deployment of the device at the site."""
        return Deployment.objects.create(
            device_type=self.device_type,
            device=device,
            site=site,
            status=DeploymentStatus.DEPLOYED.value,
            **kwargs,
        )

    def test_defaults(self):
        """Default values follow the plan (PM interval, coverage, status, health)."""
        self.assertEqual(self.device_type.pm_interval_days, 180)
        self.assertEqual(self.site_a.coverage, 'FULL')

        dep = Deployment.objects.create(device_type=self.device_type)
        self.assertEqual(dep.status, DeploymentStatus.PLANNED.value)
        self.assertEqual(dep.health, HealthStatus.UNKNOWN.value)
        self.assertIn(dep.status, DeploymentStatusGroups.PIPELINE)

    def test_references(self):
        """References are generated from the default patterns."""
        self.assertEqual(self.site_a.reference, 'ST-001')
        self.assertEqual(self.site_b.reference, 'ST-002')
        self.assertEqual(self.site_b.reference_int, 2)
        self.assertEqual(str(self.site_a), 'ST-001 - Algarve #1')

        dep = Deployment.objects.create(device_type=self.device_type)
        self.assertEqual(dep.reference, 'DP-0001')

        task = MaintenanceTask.objects.create(device=self.device_a)
        self.assertEqual(task.reference, 'MT-0001')
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)

        trip = FieldTrip.objects.create(title='Algarve spring', start_date='2026-11-01')
        self.assertEqual(trip.reference, 'TRIP-0001')

        alert = Alert.objects.create(alert_type=Alert.AlertType.NO_CONTACT)
        self.assertEqual(alert.reference, 'AL-00001')

        # A custom pattern is respected
        set_global_setting('FLEET_SITE_REFERENCE_PATTERN', 'SITE-{ref:04d}')
        self.assertEqual(Site.objects.create(name='Madeira').reference, 'SITE-0003')

        # An invalid pattern is rejected
        with self.assertRaises(ValidationError):
            set_global_setting('FLEET_TASK_REFERENCE_PATTERN', 'MT-no-ref')

    def test_one_active_deployment_per_device(self):
        """A device can only be DEPLOYED once at a time."""
        self.deploy(self.device_a, self.site_a)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.deploy(self.device_a, self.site_b)

        # Closed deployments of the same device are allowed
        Deployment.objects.create(
            device_type=self.device_type,
            device=self.device_a,
            site=self.site_b,
            status=DeploymentStatus.RECOVERED.value,
        )

    def test_one_active_deployment_per_site(self):
        """A site can only have one DEPLOYED deployment at a time."""
        old = self.deploy(self.device_a, self.site_a)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.deploy(self.device_b, self.site_a)

        # Once the old device is recovered, the replacement can be deployed
        old.status = DeploymentStatus.RECOVERED.value
        old.save()

        new = self.deploy(
            self.device_b,
            self.site_a,
            deployment_type=Deployment.DeploymentType.REPLACEMENT,
            replaces=old,
        )

        self.assertEqual(list(old.replaced_by.all()), [new])
        self.assertEqual(self.site_a.deployments.count(), 2)

    def test_build_link_unique(self):
        """A build order is linked to at most one deployment."""
        from build.models import Build

        build = Build.objects.create(part=self.part, quantity=1, title='Aurora')

        dep = Deployment.objects.create(device_type=self.device_type, build=build)
        self.assertEqual(build.fleet_deployment, dep)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Deployment.objects.create(device_type=self.device_type, build=build)

        # Deployments without a build are not affected
        Deployment.objects.create(device_type=self.device_type)
        Deployment.objects.create(device_type=self.device_type)

    def test_alert_dedupe(self):
        """Only one unresolved alert may exist for each dedupe key."""
        key = 'dp:1:STREAM_MISSING:hydrophone'

        alert = Alert.objects.create(alert_type='STREAM_MISSING', dedupe_key=key)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(alert_type='STREAM_MISSING', dedupe_key=key)

        # Acknowledged is still unresolved
        alert.status = AlertStatus.ACKNOWLEDGED.value
        alert.save()

        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(alert_type='STREAM_MISSING', dedupe_key=key)

        # Once resolved, the condition can raise a new alert
        alert.status = AlertStatus.RESOLVED.value
        alert.save()

        Alert.objects.create(alert_type='STREAM_MISSING', dedupe_key=key)

        # Alerts without a dedupe key are not constrained
        Alert.objects.create(alert_type='PM_DUE')
        Alert.objects.create(alert_type='PM_DUE')

    def test_device_link(self):
        """Platform ids are unique, but may be left blank until configured."""
        DeviceLink.objects.create(stock_item=self.device_a)
        DeviceLink.objects.create(stock_item=self.device_b)

        self.device_a.fleet_link.platform_id = 'aurora-161'
        self.device_a.fleet_link.save()

        self.device_b.fleet_link.platform_id = 'aurora-161'

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.device_b.fleet_link.save()

    def test_geofence_polygon(self):
        """Geofence polygons must be a list of at least three distinct points."""
        validate = fleet.validators.validate_geofence_polygon

        validate(None)
        validate([[37.0, -7.9], [37.1, -7.9], [37.1, -8.0]])
        validate([[37.0, -7.9], [37.1, -7.9], [37.1, -8.0], [37.0, -7.9]])

        for invalid in [
            'not a list',
            [[37.0, -7.9], [37.1, -7.9]],
            [[37.0, -7.9], [37.0, -7.9], [37.0, -7.9], [37.1, -7.9]],
            [[37.0, -7.9], [37.1], [37.1, -8.0]],
            [[37.0, -7.9], [37.1, -7.9], [91.0, -8.0]],
            [[37.0, -7.9], [37.1, -7.9], ['a', -8.0]],
        ]:
            with self.assertRaises(ValidationError):
                validate(invalid)

        self.site_b.geofence_polygon = [[37.0, -7.9], [37.1, -7.9]]

        with self.assertRaises(ValidationError):
            self.site_b.full_clean()

    def test_email_list_setting(self):
        """The alert email setting accepts a comma-separated list of addresses."""
        set_global_setting('FLEET_ALERT_EMAILS', 'ops@example.com, fleet@example.com')

        with self.assertRaises(ValidationError):
            set_global_setting('FLEET_ALERT_EMAILS', 'ops@example.com, not-an-email')

    def test_fault_codes_seeded(self):
        """The data migration seeds the default fault codes."""
        self.assertEqual(FaultCode.objects.count(), 14)

        code = FaultCode.objects.get(code='BATTERY_DEGRADATION')
        self.assertEqual(code.category, FaultCode.Category.POWER)
        self.assertTrue(code.active)

    def test_report_context(self):
        """Report contexts are available for deployments, tasks and trips."""
        dep = self.deploy(self.device_a, self.site_a)
        task = MaintenanceTask.objects.create(
            device=self.device_a, deployment=dep, site=self.site_a
        )
        trip = FieldTrip.objects.create(title='Algarve spring', start_date='2026-11-01')

        self.assertEqual(dep.report_context()['site'], self.site_a)
        self.assertEqual(list(dep.report_context()['tasks']), [task])
        self.assertEqual(task.report_context()['device'], self.device_a)
        self.assertEqual(trip.report_context()['reference'], trip.reference)

    def test_stock_history_codes(self):
        """Fleet stock history codes use the free 130+ range."""
        self.assertEqual(StockHistoryCode.FLEET_DEPLOYED.value, 130)
        self.assertEqual(StockHistoryCode.FLEET_RECOVERED.value, 131)
        self.assertEqual(StockHistoryCode.FLEET_MAINTENANCE.value, 132)

    def test_ruleset(self):
        """Every fleet table is covered by the fleet role."""
        fleet_tables = {
            model._meta.db_table for model in apps.get_app_config('fleet').get_models()
        }

        self.assertEqual(set(get_ruleset_models()[RuleSetEnum.FLEET]), fleet_tables)


class FleetStatusAPITest(InvenTreeAPITestCase):
    """Tests for the Fleet status code endpoints."""

    def test_status_endpoints(self):
        """Each fleet status class is exposed through a StatusView."""
        for name, cls, value in [
            ('deployment', 'DeploymentStatus', 50),
            ('task', 'TaskStatus', 40),
            ('trip', 'TripStatus', 50),
            ('alert', 'AlertStatus', 30),
        ]:
            response = self.get(reverse(f'api-fleet-{name}-status-codes'))

            self.assertEqual(response.data['status_class'], cls)
            self.assertIn(value, [v['key'] for v in response.data['values'].values()])
