"""API tests for the fleet app (phases P1, P2 and P2b)."""

from datetime import timedelta
from unittest import mock

from django.urls import include, path, reverse
from django.utils import timezone

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from build.models import Build
from common.settings import set_global_setting
from company.models import Company
from fleet.api import fleet_api_urls
from fleet.integrations.base import DeviceStatus, Position, StreamStatus
from fleet.integrations.mock_client import MockDataPlatformClient
from fleet.models import (
    Alert,
    DataStream,
    Deployment,
    DeviceLink,
    FleetDeviceType,
    MaintenanceTask,
    Site,
    StreamTemplate,
)
from fleet.services import deployment as deployment_service
from fleet.services import monitoring, pipeline
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DataStreamStatus,
    DeploymentStatus,
    HealthStatus,
    TaskStatus,
)
from InvenTree.helpers import current_date
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part
from stock.models import StockItem, StockLocation


class FleetAPITestBase(InvenTreeAPITestCase):
    """Common data for the fleet API tests."""

    roles = ['fleet.view']

    @classmethod
    def setUpTestData(cls):
        """Create a device type, a site, a customer and a workshop."""
        super().setUpTestData()

        cls.part = Part.objects.create(
            name='Aurora PI5 75m', assembly=True, trackable=True
        )
        cls.device_type = FleetDeviceType.objects.create(part=cls.part)

        cls.customer = Company.objects.create(name='BlueOasis Fleet', is_customer=True)
        cls.workshop = StockLocation.objects.create(name='Workshop')

        cls.site = Site.objects.create(
            name='Algarve #1', latitude='37.0', longitude='-7.9'
        )

    def setUp(self):
        """Configure the fleet settings."""
        super().setUp()

        set_global_setting('FLEET_DEFAULT_CUSTOMER', self.customer.pk)
        set_global_setting('FLEET_WORKSHOP_LOCATION', self.workshop.pk)

    def make_device(self, serial):
        """Create a serialized unit in stock."""
        return StockItem.objects.create(
            part=self.part, quantity=1, serial=serial, location=self.workshop
        )

    def action_url(self, deployment, action):
        """Return the URL of a deployment action."""
        return reverse(f'api-fleet-deployment-{action}', kwargs={'pk': deployment.pk})


class FleetPermissionTest(FleetAPITestBase):
    """Role checks on the fleet API."""

    roles = []

    def test_no_role(self):
        """Users without the fleet role cannot use the fleet API."""
        for name in [
            'api-fleet-site-list',
            'api-fleet-deployment-list',
            'api-fleet-device-type-list',
            'api-fleet-calendar',
            'api-fleet-overview',
        ]:
            self.get(reverse(name), expected_code=403)

        dep = pipeline.create_planned(self.device_type, site=self.site)
        self.post(self.action_url(dep, 'deploy'), {}, expected_code=403)

    def test_view_role(self):
        """The view role can read, but not create or change."""
        self.assignRole('fleet.view')

        self.get(reverse('api-fleet-site-list'))
        self.get(reverse('api-fleet-calendar'))

        self.post(reverse('api-fleet-site-list'), {'name': 'X'}, expected_code=403)

        url = reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk})
        self.patch(url, {'name': 'Y'}, expected_code=403)
        self.delete(url, expected_code=403)

        dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(dep, self.make_device('AUR-1'))
        self.post(self.action_url(dep, 'deploy'), {}, expected_code=403)
        self.post(
            self.action_url(dep, 'set-position'),
            {'latitude': '38.7', 'longitude': '-9.1'},
            expected_code=403,
        )

    def test_change_role(self):
        """Technicians (fleet change) run actions but cannot delete sites."""
        self.assignRole('fleet.view')
        self.assignRole('fleet.change')

        dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(dep, self.make_device('AUR-2'))

        # Deploying moves stock, so the stock change role is needed too
        response = self.post(self.action_url(dep, 'deploy'), {}, expected_code=403)
        self.assertIn('stock.change', str(response.data))

        self.assignRole('stock.change')
        response = self.post(self.action_url(dep, 'deploy'), {}, expected_code=200)
        self.assertEqual(response.data['status'], DeploymentStatus.DEPLOYED.value)

        url = reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk})
        self.patch(url, {'name': 'Algarve #1b'})
        self.delete(url, expected_code=403)

        # Creating a build order needs fleet add and build add
        planned = pipeline.create_planned(self.device_type)
        self.post(self.action_url(planned, 'create-build'), {}, expected_code=403)


class SiteAPITest(FleetAPITestBase):
    """Site endpoints."""

    roles = ['fleet.view', 'fleet.add', 'fleet.change', 'fleet.delete']

    def test_create_and_filter(self):
        """Create sites, and filter by active deployment."""
        url = reverse('api-fleet-site-list')

        response = self.post(
            url,
            {
                'name': 'Madeira #1',
                'latitude': '32.650000',
                'longitude': '-16.900000',
                'coverage': 'NO_SERVICE',
                'client': self.customer.pk,
            },
        )

        self.assertEqual(response.data['reference'], 'ST-002')
        self.assertEqual(response.data['client_detail']['name'], 'BlueOasis Fleet')
        self.assertIsNone(response.data['active_deployment'])

        # Invalid geofence polygon
        self.post(
            url, {'name': 'Bad', 'geofence_polygon': [[37.0, -7.9]]}, expected_code=400
        )

        dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(dep, self.make_device('AUR-3'))
        deployment_service.deploy(dep)

        response = self.get(url, {'has_active_deployment': True})
        self.assertEqual([site['pk'] for site in response.data], [self.site.pk])
        self.assertEqual(response.data[0]['active_deployment_reference'], dep.reference)

        response = self.get(url, {'has_active_deployment': False})
        self.assertEqual(len(response.data), 1)

        response = self.get(url, {'search': 'madeira'})
        self.assertEqual(len(response.data), 1)

    def test_delete(self):
        """Sites with deployments cannot be deleted."""
        pipeline.create_planned(self.device_type, site=self.site)

        url = reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk})
        self.delete(url, expected_code=400)

        empty = Site.objects.create(name='Empty')
        self.delete(reverse('api-fleet-site-detail', kwargs={'pk': empty.pk}))


class DeviceTypeAPITest(FleetAPITestBase):
    """Device type and stream template endpoints."""

    roles = ['fleet.view', 'fleet.add', 'fleet.change', 'fleet.delete']

    def test_device_type(self):
        """Device types must be trackable assemblies."""
        url = reverse('api-fleet-device-type-list')

        plain = Part.objects.create(name='Anode', component=True)
        self.post(url, {'part': plain.pk}, expected_code=400)

        hydrophone = Part.objects.create(
            name='Hydrophone 3m', assembly=True, trackable=True
        )
        response = self.post(url, {'part': hydrophone.pk, 'pm_interval_days': 120})
        self.assertEqual(response.data['part_detail']['name'], 'Hydrophone 3m')
        self.assertEqual(response.data['pm_interval_days'], 120)

        # Stream templates
        streams = reverse('api-fleet-stream-template-list')
        self.post(
            streams,
            {
                'device_type': response.data['pk'],
                'key': 'hydrophone',
                'essential': True,
                'expected_interval_minutes': 10,
            },
        )

        # The key is unique per device type
        self.post(
            streams,
            {'device_type': response.data['pk'], 'key': 'hydrophone'},
            expected_code=400,
        )

        response = self.get(url, {'search': 'hydrophone'})
        self.assertEqual(response.data[0]['stream_count'], 1)

        # Device types with deployments cannot be deleted
        pipeline.create_planned(self.device_type)
        self.delete(
            reverse('api-fleet-device-type-detail', kwargs={'pk': self.device_type.pk}),
            expected_code=400,
        )


class DeploymentAPITest(FleetAPITestBase):
    """Deployment endpoints and actions."""

    roles = [
        'fleet.view',
        'fleet.add',
        'fleet.change',
        'fleet.delete',
        'build.add',
        'stock.change',
    ]

    def test_plan_and_filter(self):
        """Plan a deployment; the pipeline filters find it."""
        url = reverse('api-fleet-deployment-list')

        self.site.coverage = 'THIRD_PARTY'
        self.site.save()

        response = self.post(
            url, {'device_type': self.device_type.pk, 'site': self.site.pk}
        )

        self.assertEqual(response.data['status'], DeploymentStatus.PLANNED.value)
        self.assertEqual(response.data['coverage'], 'THIRD_PARTY')
        self.assertEqual(response.data['site_detail']['name'], 'Algarve #1')

        # Status is read-only
        detail = reverse(
            'api-fleet-deployment-detail', kwargs={'pk': response.data['pk']}
        )
        self.patch(detail, {'status': DeploymentStatus.DEPLOYED.value})
        self.assertEqual(
            Deployment.objects.get(pk=response.data['pk']).status,
            DeploymentStatus.PLANNED.value,
        )

        # A BO issued for the device type enters the pipeline unscheduled
        build = Build.objects.create(part=self.part, quantity=1, title='Aurora')
        build.issue_build()

        response = self.get(url, {'unscheduled': True})
        self.assertEqual(len(response.data), 2)

        self.assertEqual(len(self.get(url, {'pipeline': True}).data), 2)
        self.assertEqual(len(self.get(url, {'active': True}).data), 0)
        self.assertEqual(
            len(self.get(url, {'status': DeploymentStatus.IN_PRODUCTION.value}).data), 1
        )
        self.assertEqual(len(self.get(url, {'build': build.pk}).data), 1)

        # Setting a target date schedules it
        dep = build.fleet_deployment
        target = current_date() + timedelta(days=3)
        self.patch(
            reverse('api-fleet-deployment-detail', kwargs={'pk': dep.pk}),
            {'target_date': target.isoformat(), 'site': self.site.pk},
        )

        response = self.get(url, {'unscheduled': True})
        self.assertEqual(len(response.data), 1)

        # Risks are an output option
        response = self.get(url, {'build': build.pk, 'risks': True})
        self.assertEqual(response.data[0]['risks'][0]['code'], 'NOT_READY')
        self.assertNotIn('risks', self.get(url, {'build': build.pk}).data[0])
        self.assertEqual(response.data[0]['build_detail']['reference'], build.reference)

        # Replaces only for replacements
        self.post(
            url,
            {'device_type': self.device_type.pk, 'replaces': dep.pk},
            expected_code=400,
        )

    def test_actions(self):
        """create-build, assign-device, deploy and recover."""
        dep = pipeline.create_planned(
            self.device_type,
            site=self.site,
            target_date=current_date() + timedelta(days=30),
        )

        response = self.post(
            self.action_url(dep, 'create-build'), {}, expected_code=200
        )
        build = Build.objects.get(pk=response.data['build'])
        self.assertEqual(build.quantity, 1)
        self.assertEqual(response.data['build_detail']['reference'], build.reference)

        # The open build order will produce the device
        unit = self.make_device('AUR-0500')
        self.post(
            self.action_url(dep, 'assign-device'),
            {'stock_item': unit.pk},
            expected_code=400,
        )

        build.cancel_build(None)
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.CANCELLED.value)

        dep = pipeline.create_planned(self.device_type, site=self.site)

        response = self.post(
            self.action_url(dep, 'assign-device'),
            {'stock_item': unit.pk},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], DeploymentStatus.READY.value)
        self.assertEqual(response.data['device_detail']['serial'], 'AUR-0500')
        self.assertEqual(response.data['platform_id'], '')

        response = self.post(
            self.action_url(dep, 'deploy'),
            {'latitude': '37.000100', 'longitude': '-7.900100', 'depth_m': 20},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], DeploymentStatus.DEPLOYED.value)
        self.assertIsNotNone(response.data['next_pm_date'])

        unit.refresh_from_db()
        self.assertEqual(unit.customer, self.customer)

        # Invalid input
        self.post(self.action_url(dep, 'deploy'), {}, expected_code=400)

        structural = StockLocation.objects.create(name='Field kits', structural=True)
        self.post(
            self.action_url(dep, 'recover'),
            {'location': structural.pk},
            expected_code=400,
        )

        response = self.post(
            self.action_url(dep, 'recover'),
            {'notes': 'Campaign over'},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], DeploymentStatus.RECOVERED.value)

        unit.refresh_from_db()
        self.assertEqual(unit.location, self.workshop)

        # Only planned or cancelled deployments can be deleted
        self.delete(
            reverse('api-fleet-deployment-detail', kwargs={'pk': dep.pk}),
            expected_code=400,
        )

    def test_calendar_and_overview(self):
        """Calendar events and pipeline KPI counts."""
        today = current_date()

        pipeline.create_planned(
            self.device_type, site=self.site, target_date=today + timedelta(days=10)
        )
        pipeline.create_planned(self.device_type)

        ready = pipeline.create_planned(
            self.device_type,
            site=Site.objects.create(name='Algarve #2'),
            target_date=today + timedelta(days=100),
        )
        pipeline.assign_existing_device(ready, self.make_device('AUR-0600'))

        MaintenanceTask.objects.create(
            device=ready.device,
            status=TaskStatus.SCHEDULED.value,
            scheduled_date=today + timedelta(days=2),
        )

        response = self.get(
            reverse('api-fleet-calendar'),
            {
                'start': today.isoformat(),
                'end': (today + timedelta(days=30)).isoformat(),
            },
        )

        self.assertEqual(
            [event['model_type'] for event in response.data],
            ['maintenancetask', 'deployment'],
        )
        self.assertIn('Algarve #1', response.data[1]['title'])

        response = self.get(reverse('api-fleet-overview'))
        self.assertEqual(response.data['planned'], 2)
        self.assertEqual(response.data['ready'], 1)
        self.assertEqual(response.data['to_deploy_30'], 1)
        self.assertEqual(response.data['unscheduled'], 1)
        self.assertEqual(response.data['deployed'], 0)

    def test_schema(self):
        """The fleet endpoints publish a valid OpenAPI schema."""
        schema = SchemaGenerator(
            patterns=[path('api/fleet/', include(fleet_api_urls))]
        ).get_schema(public=True)

        validate_schema(schema)

        self.assertIn('post', schema['paths']['/api/fleet/deployment/{id}/deploy/'])
        self.assertIn('get', schema['paths']['/api/fleet/calendar/'])
        self.assertIn('post', schema['paths']['/api/fleet/alert/{id}/resolve/'])
        self.assertIn('get', schema['paths']['/api/fleet/deployment/map/'])
        self.assertIn('post', schema['paths']['/api/fleet/deployment/{id}/verify/'])


class MonitoringAPITest(FleetAPITestBase):
    """Monitoring endpoints: alerts, streams, map, track, verify, overview (P2)."""

    roles = ['fleet.view', 'fleet.change']

    def setUp(self):
        """Deploy a device with an essential stream, and use the mock platform."""
        super().setUp()

        set_global_setting('FLEET_DATA_PROVIDER', 'mock')
        MockDataPlatformClient.reset()
        MockDataPlatformClient.generate = False
        self.addCleanup(MockDataPlatformClient.reset)

        patcher = mock.patch('fleet.notify.requests.post')
        patcher.start()
        self.addCleanup(patcher.stop)

        StreamTemplate.objects.create(
            device_type=self.device_type,
            key='hydrophone',
            name='Hydrophone',
            essential=True,
            expected_interval_minutes=10,
        )

        self.now = timezone.now()
        self.dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(self.dep, self.make_device('AUR-0161'))
        deployment_service.deploy(self.dep, deployed_at=self.now - timedelta(days=1))
        DeviceLink.objects.update(platform_id='AUR-0161')
        self.dep.refresh_from_db()

    def report(self, minutes_ago, latitude=None):
        """Set the mock status of the hydrophone stream (and a position)."""
        position = None

        if latitude is not None:
            position = Position(latitude=latitude, longitude=-7.9, timestamp=self.now)

        MockDataPlatformClient.statuses['AUR-0161'] = DeviceStatus(
            platform_id='AUR-0161',
            streams=[
                StreamStatus(
                    key='hydrophone',
                    last_seen=self.now - timedelta(minutes=minutes_ago),
                )
            ],
            position=position,
        )

    def test_alerts(self):
        """List, filter, acknowledge and resolve alerts."""
        self.report(90)
        monitoring.poll_all(force=True, now=self.now)

        url = reverse('api-fleet-alert-list')

        response = self.get(url, {'open': True})
        self.assertEqual(len(response.data), 1)

        alert = response.data[0]
        self.assertEqual(alert['alert_type'], 'STREAM_MISSING')
        self.assertEqual(alert['severity'], AlertSeverity.CRITICAL.value)
        self.assertEqual(alert['stream_key'], 'hydrophone')
        self.assertEqual(alert['device_serial'], 'AUR-0161')
        self.assertEqual(alert['deployment_detail']['reference'], self.dep.reference)
        self.assertEqual(alert['site_detail']['name'], 'Algarve #1')

        for params, count in [
            ({'severity': AlertSeverity.CRITICAL.value}, 1),
            ({'severity': AlertSeverity.WARNING.value}, 0),
            ({'deployment': self.dep.pk}, 1),
            ({'site': self.site.pk}, 1),
            ({'status': AlertStatus.OPEN.value}, 1),
            ({'open': False}, 0),
            ({'search': 'AUR-0161'}, 1),
            ({'ordering': '-severity'}, 1),
        ]:
            self.assertEqual(len(self.get(url, params).data), count, params)

        # Alerts are raised by the system only
        response = self.post(url, {'alert_type': 'NO_CONTACT'}, expected_code=None)
        self.assertIn(response.status_code, [403, 405])

        detail = reverse('api-fleet-alert-detail', kwargs={'pk': alert['pk']})
        self.assertEqual(self.get(detail).data['reference'], alert['reference'])

        response = self.post(
            reverse('api-fleet-alert-acknowledge', kwargs={'pk': alert['pk']}),
            {},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], AlertStatus.ACKNOWLEDGED.value)
        self.assertEqual(
            response.data['acknowledged_by_detail']['username'], self.user.username
        )

        resolve = reverse('api-fleet-alert-resolve', kwargs={'pk': alert['pk']})
        response = self.post(resolve, {'note': 'Cable fixed'}, expected_code=200)

        self.assertEqual(response.data['status'], AlertStatus.RESOLVED.value)
        self.assertEqual(response.data['resolution'], 'MANUAL')
        self.assertEqual(response.data['data']['resolution_note'], 'Cable fixed')

        # Resolving twice is an error
        self.post(resolve, {}, expected_code=400)

    def test_alert_permissions(self):
        """Acknowledging needs the fleet change role."""
        self.report(90)
        monitoring.poll_all(force=True, now=self.now)
        alert = Alert.objects.get()

        url = reverse('api-fleet-alert-acknowledge', kwargs={'pk': alert.pk})

        self.clearRoles()
        self.assignRole('fleet.view')

        self.get(reverse('api-fleet-alert-list'))
        self.post(url, {}, expected_code=403)

        self.clearRoles()
        self.get(reverse('api-fleet-alert-list'), expected_code=403)
        self.get(reverse('api-fleet-deployment-map'), expected_code=403)

    def test_streams(self):
        """List and edit the streams of a deployment."""
        url = reverse('api-fleet-stream-list')

        response = self.get(url, {'deployment': self.dep.pk})
        self.assertEqual(len(response.data), 1)

        stream = response.data[0]
        self.assertEqual(stream['key'], 'hydrophone')
        self.assertTrue(stream['essential'])

        detail = reverse('api-fleet-stream-detail', kwargs={'pk': stream['pk']})

        response = self.patch(detail, {'essential': False, 'enabled': False})
        self.assertFalse(response.data['essential'])
        self.assertFalse(response.data['enabled'])

        # The state is written by the monitoring service only
        self.patch(detail, {'state': DataStreamStatus.OK.value})
        self.assertEqual(
            DataStream.objects.get(pk=stream['pk']).state,
            DataStreamStatus.UNKNOWN.value,
        )

        self.patch(detail, {'key': 'other'}, expected_code=400)

        # Technicians cannot delete streams
        self.delete(detail, expected_code=403)

    def test_map_and_track(self):
        """The map lists deployed devices; the track lists position fixes."""
        self.report(1, latitude=37.001)
        monitoring.poll_all(force=True, now=self.now)

        response = self.get(reverse('api-fleet-deployment-map'))
        self.assertEqual(len(response.data), 1)

        row = response.data[0]
        self.assertEqual(row['reference'], self.dep.reference)
        self.assertEqual(row['site_name'], 'Algarve #1')
        self.assertEqual(row['health'], HealthStatus.OK.value)
        self.assertEqual(row['latitude'], 37.001)
        self.assertEqual(row['nominal_latitude'], 37.0)
        self.assertEqual(row['radius_m'], 200)
        self.assertEqual(row['open_alert_count'], 0)
        self.assertAlmostEqual(row['distance_from_nominal_m'], 111.2, delta=0.5)

        self.assertEqual(
            len(
                self.get(
                    reverse('api-fleet-deployment-map'),
                    {'health': HealthStatus.CRITICAL.value},
                ).data
            ),
            0,
        )

        url = reverse('api-fleet-deployment-track', kwargs={'pk': self.dep.pk})

        response = self.get(url)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(float(response.data[0]['latitude']), 37.001)

        later = (self.now + timedelta(minutes=1)).isoformat()
        self.assertEqual(len(self.get(url, {'since': later}).data), 0)
        self.get(url, {'since': 'yesterday'}, expected_code=400)

        self.get(
            reverse('api-fleet-deployment-track', kwargs={'pk': 9999}),
            expected_code=404,
        )

    def test_verify(self):
        """Verify returns the freshness of each stream, and stores nothing."""
        url = self.action_url(self.dep, 'verify')

        self.report(1)
        response = self.post(url, {}, expected_code=200)

        self.assertTrue(response.data['passed'])
        self.assertEqual(response.data['streams'][0]['key'], 'hydrophone')
        self.assertTrue(response.data['streams'][0]['fresh'])

        self.report(60)
        response = self.post(url, {}, expected_code=200)
        self.assertFalse(response.data['passed'])

        MockDataPlatformClient.error = 'down'
        self.post(url, {}, expected_code=400)

        # Only deployed devices
        planned = pipeline.create_planned(self.device_type)
        self.post(self.action_url(planned, 'verify'), {}, expected_code=400)

    def test_overview(self):
        """The overview counts health buckets and open alerts by severity."""
        self.report(90)
        monitoring.poll_all(force=True, now=self.now)

        data = self.get(reverse('api-fleet-overview')).data

        self.assertEqual(data['deployed'], 1)
        self.assertEqual(data['health_critical'], 1)
        self.assertEqual(data['health_ok'], 0)
        self.assertEqual(data['health_unknown'], 0)
        self.assertEqual(data['open_alerts'], 1)
        self.assertEqual(data['alerts_critical'], 1)
        self.assertEqual(data['alerts_warning'], 0)

        # Deployment filters and fields
        response = self.get(
            reverse('api-fleet-deployment-list'),
            {'health': HealthStatus.CRITICAL.value},
        )
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['open_alert_count'], 1)

        response = self.get(
            reverse('api-fleet-deployment-list'), {'has_open_alerts': True}
        )
        self.assertEqual(len(response.data), 1)


class StockDrivenAPITest(FleetAPITestBase):
    """Site optional, position per device (P2b)."""

    roles = ['fleet.view', 'fleet.change', 'stock.change']

    def deployed_without_site(self, serial):
        """Return a DEPLOYED deployment without a site or a position."""
        dep = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(dep, self.make_device(serial))
        deployment_service.deploy(dep)
        dep.refresh_from_db()
        return dep

    def test_deploy_without_site(self):
        """The deploy action works without a site; the position may stay empty."""
        dep = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(dep, self.make_device('AUR-0800'))

        data = self.post(self.action_url(dep, 'deploy'), {}, expected_code=200).data
        self.assertEqual(data['status'], DeploymentStatus.DEPLOYED.value)
        self.assertIsNone(data['site'])
        self.assertIsNone(data['latitude'])

    def test_set_position_and_filters(self):
        """Set position action, has_position / has_site filters, overview KPIs."""
        dep = self.deployed_without_site('AUR-0810')
        other = self.deployed_without_site('AUR-0811')
        url = reverse('api-fleet-deployment-list')

        overview = self.get(reverse('api-fleet-overview')).data
        self.assertEqual(overview['no_position'], 2)
        self.assertEqual(overview['no_site'], 2)

        # Latitude and longitude are required, and validated
        self.post(self.action_url(dep, 'set-position'), {}, expected_code=400)
        self.post(
            self.action_url(dep, 'set-position'),
            {'latitude': 91, 'longitude': 0},
            expected_code=400,
        )

        data = self.post(
            self.action_url(dep, 'set-position'),
            {'latitude': '38.7', 'longitude': '-9.1', 'geofence_radius_m': 250},
            expected_code=200,
        ).data
        self.assertEqual(float(data['latitude']), 38.7)
        self.assertEqual(data['geofence_radius_m'], 250)

        response = self.get(url, {'has_position': True})
        self.assertEqual([row['pk'] for row in response.data], [dep.pk])

        response = self.get(url, {'has_position': False})
        self.assertEqual([row['pk'] for row in response.data], [other.pk])

        response = self.get(url, {'has_site': False})
        self.assertEqual(len(response.data), 2)

        overview = self.get(reverse('api-fleet-overview')).data
        self.assertEqual(overview['no_position'], 1)

        # The map lists both; the one without a position has no coordinates
        rows = {
            row['pk']: row for row in self.get(reverse('api-fleet-deployment-map')).data
        }
        self.assertEqual(rows[dep.pk]['nominal_latitude'], 38.7)
        self.assertIsNone(rows[other.pk]['latitude'])

    def test_set_site_later(self):
        """Setting a site fills an empty position, but never overwrites one."""
        dep = self.deployed_without_site('AUR-0820')
        positioned = self.deployed_without_site('AUR-0821')
        deployment_service.set_position(positioned, latitude='38', longitude='-9')

        def detail(deployment):
            return reverse('api-fleet-deployment-detail', kwargs={'pk': deployment.pk})

        data = self.patch(detail(dep), {'site': self.site.pk}, expected_code=200).data
        self.assertEqual(float(data['latitude']), 37.0)
        self.assertEqual(float(data['longitude']), -7.9)

        # The site is now occupied by an active deployment
        self.patch(detail(positioned), {'site': self.site.pk}, expected_code=400)

        other_site = Site.objects.create(
            name='Sines #2', latitude='37.9', longitude='-8.8'
        )
        data = self.patch(
            detail(positioned), {'site': other_site.pk}, expected_code=200
        ).data
        self.assertEqual(float(data['latitude']), 38.0)
