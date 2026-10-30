"""API tests for the fleet app (phases P1, P2 and P2b)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.urls import NoReverseMatch, include, path, reverse
from django.utils import timezone

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from build.models import Build
from build.status_codes import BuildStatus
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
from fleet.services.stock_sync import sync_stock_deployments
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DataStreamStatus,
    DeploymentStatus,
    HealthStatus,
    TaskStatus,
)
from fleet.test_support import new_site, plan_unit, sell_unit
from InvenTree.helpers import current_date
from InvenTree.unit_test import InvenTreeAPITestCase
from order.models import ReturnOrderLineItem, SalesOrder
from order.status_codes import ReturnOrderStatus
from part.models import Part
from stock.models import StockItem, StockLocation
from stock.status_codes import StockStatus


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

        dep = plan_unit(self.make_device('AUR-0'), self.device_type, site=self.site)
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

        dep = plan_unit(self.make_device('AUR-1'), self.device_type, site=self.site)
        self.post(self.action_url(dep, 'deploy'), {}, expected_code=403)
        self.post(
            reverse('api-fleet-site-move', kwargs={'pk': self.site.pk}),
            {'latitude': '38.7', 'longitude': '-9.1', 'comment': 'Moved'},
            expected_code=403,
        )
        self.get(reverse('api-fleet-site-location-list'))

    def test_change_role(self):
        """Technicians (fleet change) run actions but cannot delete sites."""
        self.assignRole('fleet.view')
        self.assignRole('fleet.change')

        dep = plan_unit(self.make_device('AUR-2'), self.device_type, site=self.site)

        # Deploying moves stock, so the stock change role is needed too
        response = self.post(self.action_url(dep, 'deploy'), {}, expected_code=403)
        self.assertIn('stock.change', str(response.data))

        self.assignRole('stock.change')
        response = self.post(self.action_url(dep, 'deploy'), {}, expected_code=200)
        self.assertEqual(response.data['status'], DeploymentStatus.DEPLOYED.value)

        url = reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk})
        self.patch(url, {'name': 'Algarve #1b'})
        self.delete(url, expected_code=403)


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

        dep = plan_unit(self.make_device('AUR-3'), self.device_type, site=self.site)
        deployment_service.deploy(dep)

        response = self.get(url, {'has_active_deployment': True})
        self.assertEqual([site['pk'] for site in response.data], [self.site.pk])
        self.assertEqual(response.data[0]['active_deployment_reference'], dep.reference)

        response = self.get(url, {'has_active_deployment': False})
        self.assertEqual(len(response.data), 1)

        response = self.get(url, {'search': 'madeira'})
        self.assertEqual(len(response.data), 1)

    def test_coordinate_formats(self):
        """Coordinates are accepted as DD, DDM or DMS, and stored as decimal degrees."""
        url = reverse('api-fleet-site-list')

        response = self.post(
            url,
            {
                'name': 'Lisbon DMS',
                'latitude': '38°43\'20.12"N',
                'longitude': 'W 9 8.3583',
            },
        )

        site = Site.objects.get(pk=response.data['pk'])
        self.assertEqual(site.latitude, Decimal('38.7222555556'))
        self.assertEqual(site.longitude, Decimal('-9.1393050000'))

        # A site without a location gets one by editing it; plain numbers
        # still work, rounded to the stored precision
        empty = Site.objects.create(name='Lisbon later')
        detail = reverse('api-fleet-site-detail', kwargs={'pk': empty.pk})
        self.patch(detail, {'latitude': 38.722255555555555, 'longitude': '-9,5'})
        empty.refresh_from_db()
        self.assertEqual(empty.latitude, Decimal('38.7222555556'))
        self.assertEqual(empty.longitude, Decimal('-9.5'))

        # Invalid values are rejected with a clear message
        move = reverse('api-fleet-site-move', kwargs={'pk': site.pk})

        def bad(**values):
            return self.post(
                move,
                {'latitude': '38', 'longitude': '-9', 'comment': 'x', **values},
                expected_code=400,
            )

        response = bad(latitude='38 75 N')
        self.assertIn('below 60', str(response.data['latitude']))
        bad(latitude='38 E')
        bad(longitude='181')

        # The form renders a coordinate input
        fields = self.options(url).data['actions']['POST']
        self.assertEqual(fields['latitude']['type'], 'coordinate')
        self.assertEqual(fields['latitude']['axis'], 'latitude')
        self.assertEqual(fields['longitude']['axis'], 'longitude')

    def test_delete(self):
        """Sites with deployments cannot be deleted."""
        plan_unit(self.make_device('AUR-4'), self.device_type, site=self.site)

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
        plan_unit(self.make_device('AUR-5'), self.device_type)
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
        """New deployment: choose an undeployed unit and plan it; the filters find it."""
        url = reverse('api-fleet-deployment-list')

        self.site.coverage = 'THIRD_PARTY'
        self.site.save()

        # The fleet never creates a deployment out of nothing
        self.post(url, {'device_type': self.device_type.pk}, expected_code=405)

        # A unit in stock gets its row in the deployment list (stock sync)
        unit = self.make_device('AUR-0900')
        sync_stock_deployments()

        row = Deployment.objects.get(device=unit)
        self.assertEqual(row.status, DeploymentStatus.READY.value)
        self.assertIsNone(row.target_date)

        # Only a unit on a sales order is in the plan, and can be planned
        plan_url = reverse('api-fleet-plan')
        self.assertNotIn(row.pk, [r['pk'] for r in self.get(plan_url).data])
        self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': row.pk, 'site': self.site.pk},
            expected_code=400,
        )

        sell_unit(unit)
        self.assertIn(row.pk, [r['pk'] for r in self.get(plan_url).data])

        sold = self.get(url, {'sold': True}).data
        self.assertIn(row.pk, [r['pk'] for r in sold])

        # New deployment: choose the unit, plan its site
        response = self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': row.pk, 'site': self.site.pk},
            expected_code=200,
        )

        self.assertEqual(response.data['pk'], row.pk)
        self.assertEqual(response.data['status'], DeploymentStatus.READY.value)
        self.assertEqual(response.data['coverage'], 'THIRD_PARTY')
        self.assertEqual(response.data['site_detail']['name'], 'Algarve #1')
        self.assertIsNone(response.data['return_order'])

        self.post(reverse('api-fleet-deployment-new'), {}, expected_code=400)

        # Status is read-only
        detail = reverse('api-fleet-deployment-detail', kwargs={'pk': row.pk})
        self.patch(detail, {'status': DeploymentStatus.DEPLOYED.value})
        self.assertEqual(
            Deployment.objects.get(pk=row.pk).status, DeploymentStatus.READY.value
        )

        # A BO issued for a sale of the device type enters the pipeline unscheduled
        build = Build.objects.create(
            part=self.part,
            quantity=1,
            title='Aurora',
            sales_order=SalesOrder.objects.create(customer=self.customer),
        )
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
        self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': dep.pk, 'target_date': target.isoformat()},
            expected_code=200,
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
            reverse('api-fleet-deployment-new'),
            {'deployment': row.pk, 'replaces': dep.pk},
            expected_code=400,
        )

    def test_actions(self):
        """new, deploy and recover (a return order); the build order is read only."""
        # The fleet cannot create build orders
        with self.assertRaises(NoReverseMatch):
            reverse('api-fleet-deployment-create-build', kwargs={'pk': 1})

        # Manufacturing issues the build order; the fleet sees its status and
        # target date (an outside view)
        build = Build.objects.create(
            part=self.part,
            quantity=1,
            title='Aurora',
            target_date=current_date() + timedelta(days=20),
        )
        build.issue_build()
        dep = build.fleet_deployment

        url = reverse('api-fleet-deployment-detail', kwargs={'pk': dep.pk})
        response = self.get(url)
        detail = response.data['build_detail']
        self.assertEqual(detail['reference'], build.reference)
        self.assertEqual(detail['status'], BuildStatus.PRODUCTION.value)
        self.assertEqual(detail['target_date'], build.target_date.isoformat())

        # The build order cannot be changed through the deployment
        self.patch(url, {'build': None})
        dep.refresh_from_db()
        self.assertEqual(dep.build, build)

        # Its row is the build order's; a finished unit gets one of its own
        build.cancel_build(None)
        dep.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.CANCELLED.value)

        unit = self.make_device('AUR-0500')
        sell_unit(unit)
        sync_stock_deployments()

        dep = Deployment.objects.get(device=unit, status=DeploymentStatus.READY.value)
        response = self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': dep.pk, 'site': self.site.pk},
            expected_code=200,
        )
        self.assertEqual(response.data['device_detail']['serial'], 'AUR-0500')
        self.assertEqual(response.data['platform_id'], '')

        response = self.post(self.action_url(dep, 'deploy'), {}, expected_code=200)
        self.assertEqual(response.data['status'], DeploymentStatus.DEPLOYED.value)
        self.assertIsNotNone(response.data['next_pm_date'])

        unit.refresh_from_db()
        self.assertEqual(unit.customer, self.customer)

        # Invalid input
        self.post(self.action_url(dep, 'deploy'), {}, expected_code=400)

        # Recover: a return order brings the device to the office
        response = self.post(
            self.action_url(dep, 'recover'),
            {'notes': 'Campaign over'},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], DeploymentStatus.RECOVERED.value)

        unit.refresh_from_db()
        self.assertEqual(unit.customer, self.customer)

        line = ReturnOrderLineItem.objects.get(item=unit)
        self.assertEqual(line.order.customer, self.customer)
        self.assertEqual(line.order.status, ReturnOrderStatus.IN_PROGRESS.value)

        # Its next row is in the deployment list straight away (returning)
        nxt = Deployment.objects.get(device=unit, status=DeploymentStatus.READY.value)
        detail = reverse('api-fleet-deployment-detail', kwargs={'pk': nxt.pk})
        response = self.get(detail)
        self.assertEqual(response.data['return_order'], line.order.pk)
        self.assertEqual(response.data['return_order_reference'], line.order.reference)

        # Its old sale does not count: it cannot be planned without a new
        # one, nor deployed before the office receives it
        self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': nxt.pk, 'target_date': current_date().isoformat()},
            expected_code=400,
        )
        response = self.post(self.action_url(nxt, 'deploy'), {}, expected_code=400)
        self.assertIn(line.order.reference, str(response.data))

        line.order.receive_line_item(
            line, self.workshop, None, status=StockStatus.OK.value
        )
        unit.refresh_from_db()
        self.assertIsNone(unit.customer)
        self.assertEqual(unit.location, self.workshop)
        self.assertIsNone(self.get(detail).data['return_order'])

        # Sold again, it can be planned
        sell_unit(unit)
        self.post(
            reverse('api-fleet-deployment-new'),
            {'deployment': nxt.pk, 'target_date': current_date().isoformat()},
            expected_code=200,
        )

        # Only cancelled deployments can be deleted
        self.delete(
            reverse('api-fleet-deployment-detail', kwargs={'pk': dep.pk}),
            expected_code=400,
        )
        self.delete(detail, expected_code=400)

    def test_calendar_and_overview(self):
        """Calendar events and pipeline KPI counts."""
        today = current_date()

        # Sold devices whose build order is pending (PLANNED)
        order = SalesOrder.objects.create(customer=self.customer)
        sold = Build.objects.create(
            part=self.part, quantity=1, title='Sold', sales_order=order
        )
        Build.objects.create(
            part=self.part, quantity=1, title='Sold', sales_order=order
        )
        pipeline.new_deployment(
            sold.fleet_deployment,
            site=self.site,
            target_date=today + timedelta(days=10),
        )

        ready = plan_unit(
            self.make_device('AUR-0600'),
            self.device_type,
            site=Site.objects.create(name='Algarve #2'),
            target_date=today + timedelta(days=100),
        )

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

    def test_plan_list(self):
        """One list of deployments and maintenance, in plan order."""
        today = current_date()
        set_global_setting('FLEET_PLAN_HORIZON_DAYS', 45)

        def planned(serial, days=None, site=None):
            target = today + timedelta(days=days) if days is not None else None
            return plan_unit(
                self.make_device(serial),
                self.device_type,
                site=site,
                target_date=target,
            )

        later = planned('AUR-1001', days=100)
        undated = planned('AUR-1002')
        soon = planned('AUR-1003', days=10, site=self.site)

        # A deployed device, maintenance due in 5 days
        deployed = planned('AUR-1004')
        deployment_service.deploy(deployed, site=new_site('Sines #1'))
        Deployment.objects.filter(pk=deployed.pk).update(
            next_pm_date=today + timedelta(days=5)
        )

        # A deployed device which the fleet team does not service
        other = planned('AUR-1005')
        deployment_service.deploy(other, site=new_site('Sines #2'))
        Deployment.objects.filter(pk=other.pk).update(coverage='NO_SERVICE')

        url = reverse('api-fleet-plan')
        rows = self.get(url).data

        self.assertEqual(
            [(row['kind'], row['pk'], row['group']) for row in rows],
            [
                ('maintenance', deployed.pk, 'soon'),
                ('deployment', soon.pk, 'soon'),
                ('deployment', undated.pk, 'undated'),
                ('deployment', later.pk, 'later'),
            ],
        )
        self.assertEqual(rows[0]['serial'], 'AUR-1004')
        self.assertEqual(rows[1]['site_name'], 'Algarve #1')

        # Filters and pages
        self.assertEqual(len(self.get(url, {'kind': 'maintenance'}).data), 1)
        self.assertEqual(len(self.get(url, {'kind': 'deployment'}).data), 3)
        self.assertEqual(len(self.get(url, {'site': self.site.pk}).data), 1)
        self.assertEqual(
            self.get(url, {'search': 'AUR-1002'}).data[0]['pk'], undated.pk
        )

        page = self.get(url, {'limit': 2, 'offset': 1}).data
        self.assertEqual(page['count'], 4)
        self.assertEqual([row['pk'] for row in page['results']], [soon.pk, undated.pk])

        # A shorter horizon moves the deployment in 10 days to the later group
        set_global_setting('FLEET_PLAN_HORIZON_DAYS', 7)
        rows = self.get(url).data
        self.assertEqual(
            [row['pk'] for row in rows], [deployed.pk, undated.pk, soon.pk, later.pk]
        )

    def test_plan_undated_order(self):
        """Rows without a date: ready, in production, planned, by build target date."""
        today = current_date()
        order = SalesOrder.objects.create(customer=self.customer)

        def sold_build(title, target_days=None, status=None):
            build = Build.objects.create(
                part=self.part,
                quantity=1,
                title=title,
                sales_order=order,
                target_date=today + timedelta(days=target_days)
                if target_days is not None
                else None,
            )
            deployment = build.fleet_deployment

            if status is not None:
                Deployment.objects.filter(pk=deployment.pk).update(status=status)

            return deployment

        planned = sold_build('Planned unit')
        late = sold_build('Late unit', 60, DeploymentStatus.IN_PRODUCTION.value)
        early = sold_build('Early unit', 20, DeploymentStatus.IN_PRODUCTION.value)
        ready = plan_unit(self.make_device('AUR-1101'), self.device_type)

        rows = self.get(reverse('api-fleet-plan'), {'kind': 'deployment'}).data

        self.assertEqual(
            [row['pk'] for row in rows], [ready.pk, early.pk, late.pk, planned.pk]
        )
        self.assertEqual(rows[1]['build_title'], 'Early unit')
        self.assertIsNone(rows[0]['build_title'])

        # Only the unit planned with "New Deployment" is marked as planned by hand
        self.assertEqual(
            [row['manually_planned'] for row in rows], [True, False, False, False]
        )

        url = reverse('api-fleet-plan')
        rows = self.get(url, {'manually_planned': 'true'}).data
        self.assertEqual([row['pk'] for row in rows], [ready.pk])
        rows = self.get(url, {'manually_planned': 'false'}).data
        self.assertEqual([row['pk'] for row in rows], [early.pk, late.pk, planned.pk])

    def test_schema(self):
        """The fleet endpoints publish a valid OpenAPI schema."""
        schema = SchemaGenerator(
            patterns=[path('api/fleet/', include(fleet_api_urls))]
        ).get_schema(public=True)

        validate_schema(schema)

        self.assertIn('post', schema['paths']['/api/fleet/deployment/{id}/deploy/'])
        self.assertIn('get', schema['paths']['/api/fleet/calendar/'])
        self.assertIn('get', schema['paths']['/api/fleet/plan/'])
        self.assertIn('post', schema['paths']['/api/fleet/deployment/new/'])
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
        self.dep = plan_unit(
            self.make_device('AUR-0161'), self.device_type, site=self.site
        )
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
        self.assertEqual(row['site_latitude'], 37.0)
        self.assertEqual(row['radius_m'], 200)
        self.assertEqual(row['open_alert_count'], 0)
        self.assertAlmostEqual(row['distance_from_site_m'], 111.2, delta=0.5)

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
        planned = plan_unit(self.make_device('AUR-0162'), self.device_type)
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


class SiteLocationAPITest(FleetAPITestBase):
    """The site is the only location: required to deploy, moved with a reason."""

    roles = ['fleet.view', 'fleet.change', 'stock.change']

    def deploy_url(self, serial, site=None):
        """Plan a unit (at a site, or without one); return its deploy URL."""
        dep = plan_unit(self.make_device(serial), self.device_type, site=site)
        return dep, self.action_url(dep, 'deploy')

    def test_deploy_requires_site(self):
        """Deploying needs a site: the planned one, a chosen one, or a new one."""
        dep, url = self.deploy_url('AUR-0800')

        response = self.post(url, {}, expected_code=400)
        self.assertIn('site', response.data)

        # A new site needs its location, and excludes choosing a site
        response = self.post(url, {'new_site_name': 'Sines #3'}, expected_code=400)
        self.assertIn('new_site_latitude', response.data)
        self.post(
            url,
            {
                'site': self.site.pk,
                'new_site_name': 'Sines #3',
                'new_site_latitude': '37.9',
                'new_site_longitude': '-8.8',
            },
            expected_code=400,
        )

        data = self.post(
            url,
            {
                'new_site_name': 'Sines #3',
                'new_site_latitude': '37 54 N',
                'new_site_longitude': '-8.8',
                'new_site_depth_m': 22,
            },
            expected_code=200,
        ).data
        self.assertEqual(data['status'], DeploymentStatus.DEPLOYED.value)

        site = Site.objects.get(pk=data['site'])
        self.assertEqual(site.name, 'Sines #3')
        self.assertEqual(site.latitude, Decimal('37.9'))
        self.assertEqual(site.depth_m, 22)
        self.assertEqual(site.coverage, dep.coverage)

        # A chosen site replaces the planned one
        dep, url = self.deploy_url('AUR-0801', site=site)
        other = Site.objects.create(name='Sines #4', latitude='38', longitude='-9')
        data = self.post(url, {'site': other.pk}, expected_code=200).data
        self.assertEqual(data['site'], other.pk)

    def test_location_filters(self):
        """has_position / no_position follow the location of the site."""
        located = self.deploy_url('AUR-0810', site=self.site)[0]
        deployment_service.deploy(located)
        unlocated = self.deploy_url('AUR-0811', site=new_site('Sines #5'))[0]
        deployment_service.deploy(unlocated)
        url = reverse('api-fleet-deployment-list')

        overview = self.get(reverse('api-fleet-overview')).data
        self.assertEqual(overview['no_position'], 1)
        self.assertEqual(overview['no_site'], 0)

        response = self.get(url, {'has_position': True})
        self.assertEqual([row['pk'] for row in response.data], [located.pk])

        response = self.get(url, {'has_position': False})
        self.assertEqual([row['pk'] for row in response.data], [unlocated.pk])

        # The map lists both; the one without a location has no coordinates
        rows = {
            row['pk']: row for row in self.get(reverse('api-fleet-deployment-map')).data
        }
        self.assertEqual(rows[located.pk]['site_latitude'], 37.0)
        self.assertIsNone(rows[unlocated.pk]['latitude'])

    def test_move_site(self):
        """Moving a site keeps the old location and the reason in its history."""
        url = reverse('api-fleet-site-move', kwargs={'pk': self.site.pk})
        detail = reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk})
        history = reverse('api-fleet-site-location-list')

        # The location is no longer edited directly
        response = self.patch(detail, {'latitude': '37.1'}, expected_code=400)
        self.assertIn('Move Site', str(response.data['latitude']))
        self.patch(detail, {'latitude': '37.0', 'name': 'Algarve #1b'})

        # A reason is required; the date cannot be in the future
        self.post(url, {'latitude': '37.01', 'longitude': '-7.9'}, expected_code=400)
        self.post(
            url,
            {
                'latitude': '37.01',
                'longitude': '-7.9',
                'comment': 'Shipping lane',
                'effective_at': (timezone.now() + timedelta(days=1)).isoformat(),
            },
            expected_code=400,
        )

        # The same location is not a move
        self.post(
            url,
            {'latitude': '37.0', 'longitude': '-7.9', 'comment': 'Nothing'},
            expected_code=400,
        )

        moved_at = timezone.now() - timedelta(days=2)
        data = self.post(
            url,
            {
                'latitude': '37.01',
                'longitude': '-7.9',
                'comment': 'Moved 1.1 km north, away from the shipping lane',
                'effective_at': moved_at.isoformat(),
            },
            expected_code=200,
        ).data
        self.assertEqual(Decimal(data['latitude']), Decimal('37.01'))

        rows = self.get(history, {'site': self.site.pk}).data
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]['old_latitude']), Decimal('37.0'))
        self.assertEqual(Decimal(rows[0]['latitude']), Decimal('37.01'))
        self.assertAlmostEqual(rows[0]['distance_m'], 1112, delta=2)
        self.assertEqual(rows[0]['created_by_detail']['username'], self.user.username)

        # A move cannot be dated before the last one
        self.post(
            url,
            {
                'latitude': '37.02',
                'longitude': '-7.9',
                'comment': 'Back',
                'effective_at': (moved_at - timedelta(days=1)).isoformat(),
            },
            expected_code=400,
        )

    def test_change_site_later(self):
        """The site of a deployed device can be changed, to a free site only."""
        dep = self.deploy_url('AUR-0820', site=new_site('Sines #6'))[0]
        deployment_service.deploy(dep)
        busy = self.deploy_url('AUR-0821', site=self.site)[0]
        deployment_service.deploy(busy)

        detail = reverse('api-fleet-deployment-detail', kwargs={'pk': dep.pk})

        # The site is occupied by an active deployment
        self.patch(detail, {'site': self.site.pk}, expected_code=400)

        other_site = Site.objects.create(
            name='Sines #7', latitude='37.9', longitude='-8.8'
        )
        data = self.patch(detail, {'site': other_site.pk}, expected_code=200).data
        self.assertEqual(data['site'], other_site.pk)
        self.assertNotIn('latitude', data)
