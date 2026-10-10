"""Tests for fleet maintenance and planning (phase P3).

Covers the acceptance scenarios 5 (scheduled maintenance with replacement,
without a field trip), 6 (remove without replacement), 8 (NO_SERVICE coverage:
no PM proposals or PM alerts) and 9 (permissions) from FLEET_PLAN.md section 6.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import include, path, reverse
from django.utils import timezone

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from build.models import Build
from build.status_codes import BuildStatus
from common.settings import set_global_setting
from company.models import Company
from fleet.api import fleet_api_urls
from fleet.integrations.base import DeviceStatus, StreamStatus
from fleet.integrations.mock_client import MockDataPlatformClient
from fleet.models import (
    Alert,
    ChecklistResult,
    ChecklistTemplateItem,
    Coverage,
    DataStream,
    Deployment,
    DeviceLink,
    FaultCode,
    FleetDeviceType,
    InternalState,
    KitTemplateLine,
    MaintenanceAction,
    MaintenanceTask,
    Site,
    StreamTemplate,
    TaskType,
)
from fleet.services import deployment as deployment_service
from fleet.services import (
    device_state,
    maintenance,
    monitoring,
    pipeline,
    planning,
    trips,
)
from fleet.status_codes import AlertSeverity, AlertStatus, DeploymentStatus, TaskStatus
from fleet.tasks import fleet_daily_planning
from InvenTree.helpers import current_date
from InvenTree.unit_test import InvenTreeAPITestCase, InvenTreeTestCase
from part.models import Part
from stock.models import StockItem, StockItemTracking, StockLocation
from stock.status_codes import StockHistoryCode, StockStatus

WEBHOOK = 'https://example.webhook.office.com/workflows/fleet'


class MaintenanceDataMixin:
    """Shared data: a fleet device type with streams, a checklist and parts."""

    @classmethod
    def create_fleet_data(cls):
        """Create the device type, the site, the parts and the locations."""
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
            device_type=cls.device_type,
            key='battery',
            name='Battery',
            expected_interval_minutes=60,
        )

        # Preventive items, an item for every task type, and a corrective one
        ChecklistTemplateItem.objects.create(
            device_type=cls.device_type,
            sequence=1,
            text='Measure battery voltage',
            kind=ChecklistTemplateItem.Kind.MEASUREMENT,
            unit='V',
        )
        ChecklistTemplateItem.objects.create(
            device_type=cls.device_type,
            sequence=2,
            text='Inspect O-rings',
            task_type='',
        )
        ChecklistTemplateItem.objects.create(
            device_type=cls.device_type,
            sequence=3,
            text='Photo of the hull',
            kind=ChecklistTemplateItem.Kind.PHOTO,
            required=False,
        )
        ChecklistTemplateItem.objects.create(
            device_type=cls.device_type,
            sequence=4,
            text='Find the root cause',
            task_type=TaskType.CORRECTIVE,
        )

        cls.battery = Part.objects.create(
            name='Battery pack', component=True, trackable=True
        )
        cls.oring = Part.objects.create(name='O-ring kit', component=True)

        KitTemplateLine.objects.create(
            device_type=cls.device_type, part=cls.oring, quantity=1
        )

        cls.customer = Company.objects.create(name='BlueOasis Fleet', is_customer=True)
        cls.workshop = StockLocation.objects.create(name='Workshop / To inspect')
        cls.store = StockLocation.objects.create(name='Store')

        cls.site = Site.objects.create(
            name='Algarve #1', latitude='37.000000', longitude='-7.900000'
        )

        cls.fault = FaultCode.objects.get(code='BATTERY_DEGRADATION')

    def configure(self):
        """Configure the fleet settings, the mock platform and the Teams webhook."""
        set_global_setting('FLEET_DEFAULT_CUSTOMER', self.customer.pk)
        set_global_setting('FLEET_WORKSHOP_LOCATION', self.workshop.pk)
        set_global_setting('FLEET_TEAMS_WEBHOOK_URL', WEBHOOK)
        set_global_setting('FLEET_DATA_PROVIDER', 'mock')
        set_global_setting('FLEET_PLAN_HORIZON_DAYS', 45)
        set_global_setting('FLEET_PM_DUE_WARNING_DAYS', 14)

        MockDataPlatformClient.reset()
        MockDataPlatformClient.generate = False
        self.addCleanup(MockDataPlatformClient.reset)

        patcher = mock.patch('fleet.notify.requests.post')
        self.teams = patcher.start()
        self.addCleanup(patcher.stop)

        # Notifications are sent on commit, which never happens inside a test case
        patcher = mock.patch('fleet.notify.on_commit', side_effect=lambda func: func())
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_device(self, serial, battery=None):
        """Create a built unit (from a completed build order), in stock."""
        build = Build.objects.create(
            part=self.part,
            reference=f'BO-{9000 + Build.objects.count()}',
            title='Aurora',
            quantity=1,
            completed=1,
            status=BuildStatus.COMPLETE,
        )

        device = StockItem.objects.create(
            part=self.part, quantity=1, serial=serial, build=build, location=self.store
        )

        if battery:
            StockItem.objects.create(
                part=self.battery,
                quantity=1,
                serial=battery,
                belongs_to=device,
                consumed_by=build,
            )

        return device

    def deploy(self, serial, site=None, days_ago=170, battery=None):
        """Deploy a new unit (at a site, or without one) some days ago.

        The unit has a battery installed (serial BAT-<serial> by default).
        """
        device = self.make_device(serial, battery=battery or f'BAT-{serial}')

        dep = pipeline.create_planned(self.device_type, site=site)
        pipeline.assign_existing_device(dep, device)
        deployment_service.deploy(
            dep, deployed_at=timezone.now() - timedelta(days=days_ago)
        )

        DeviceLink.objects.filter(stock_item=device).update(platform_id=serial)

        dep.refresh_from_db()
        return dep

    def report_fresh(self, dep):
        """The mock platform reports fresh data for every stream of a device."""
        platform_id = dep.device.fleet_link.platform_id

        MockDataPlatformClient.statuses[platform_id] = DeviceStatus(
            platform_id=platform_id,
            streams=[
                StreamStatus(key=stream.key, last_seen=timezone.now())
                for stream in dep.streams.all()
            ],
            position=None,
        )

    def installed(self, dep):
        """Return the battery installed in the device of a deployment."""
        return StockItem.objects.get(part=self.battery, belongs_to=dep.device)


class MaintenanceTestBase(MaintenanceDataMixin, InvenTreeTestCase):
    """Service tests for maintenance and planning."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_fleet_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def started_task(self, dep, task_type=TaskType.PREVENTIVE):
        """Return a started task for a deployment."""
        task = maintenance.create_task(dep.device, task_type=task_type)
        return maintenance.start(task, user=self.user)

    def answer_checklist(self, task, result=ChecklistResult.Result.OK):
        """Answer every checklist item of a task."""
        task.checklist.update(result=result)


class Scenario5Test(MaintenanceTestBase):
    """Scenario 5: scheduled maintenance with a replacement (without a trip)."""

    def test_scenario_5_maintenance_with_replacement(self):
        """Propose, schedule, start, replace, consume, verify and complete."""
        dep = self.deploy('AUR-0161', site=self.site, battery='BAT-0331')
        old_pm = dep.next_pm_date

        # 5.1 The next PM date is within the horizon: the daily task proposes a task
        self.assertLessEqual(old_pm, current_date() + timedelta(days=45))

        fleet_daily_planning()

        task = MaintenanceTask.objects.get(deployment=dep)
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)
        self.assertEqual(task.task_type, TaskType.PREVENTIVE)
        self.assertEqual(task.device, dep.device)
        self.assertEqual(task.site, self.site)
        self.assertEqual(task.due_date, old_pm)

        # 5.2 (without a trip) The manager schedules it
        task.scheduled_date = current_date() + timedelta(days=3)
        task.save()
        maintenance.update_schedule(task)
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)

        # A geofence alert is open on the device
        geofence, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:GEOFENCE_BREACH',
            Alert.AlertType.GEOFENCE_BREACH,
            AlertSeverity.CRITICAL.value,
            'Device is outside its geofence',
            deployment=dep,
            site=self.site,
        )

        # 5.4 The technician starts the task: preventive and "every type" items
        maintenance.start(task, user=self.user)
        task.refresh_from_db()

        self.assertEqual(task.status, TaskStatus.IN_PROGRESS.value)
        self.assertEqual(task.started_by, self.user)
        self.assertIn(self.user, task.technicians.all())
        self.assertEqual(
            list(task.checklist.values_list('text', flat=True)),
            ['Measure battery voltage', 'Inspect O-rings', 'Photo of the hull'],
        )

        # Checklist issue: battery degradation
        voltage = task.checklist.get(text='Measure battery voltage')
        voltage.result = ChecklistResult.Result.ISSUE
        voltage.value = '10.1'
        voltage.fault_code = self.fault
        voltage.save()

        # Replace BAT-0331 with BAT-0347 (no trip: removed into the workshop)
        old_battery = self.installed(dep)
        new_battery = StockItem.objects.create(
            part=self.battery, quantity=1, serial='BAT-0347', location=self.store
        )

        before = StockItemTracking.objects.count()

        action = maintenance.component_action(
            task,
            self.user,
            {
                'action': 'replace',
                'component': old_battery.pk,
                'stock_item': new_battery.pk,
                'quantity': Decimal(1),
                'disposition': 'damaged',
                'notes': 'Low voltage',
                'fault_code': self.fault,
                'checklist_result': voltage,
            },
        )

        new_battery.refresh_from_db()
        old_battery.refresh_from_db()

        self.assertEqual(new_battery.belongs_to, dep.device)
        self.assertIsNone(old_battery.belongs_to)
        self.assertEqual(old_battery.location, self.workshop)
        self.assertEqual(old_battery.status, StockStatus.DAMAGED.value)

        self.assertEqual(action.action, MaintenanceAction.Action.REPLACE)
        self.assertEqual(action.component_out, old_battery)
        self.assertEqual(action.component_in, new_battery)
        self.assertEqual(action.part, self.battery)
        self.assertEqual(action.fault_code, self.fault)
        self.assertEqual(action.checklist_result, voltage)
        self.assertEqual(action.destination, self.workshop)

        # Every new tracking row is tagged with the task, with the task reference
        rows = StockItemTracking.objects.filter(pk__gt=0).order_by('pk')[before:]
        self.assertTrue(rows)

        for row in rows:
            self.assertEqual(row.deltas.get('maintenance'), task.pk)

        tagged = StockItemTracking.objects.filter(deltas__maintenance=task.pk)
        items = set(tagged.values_list('item', flat=True))
        self.assertIn(dep.device.pk, items)
        self.assertIn(old_battery.pk, items)
        self.assertIn(new_battery.pk, items)
        self.assertTrue(any(task.reference in (row.notes or '') for row in tagged))

        # 5.5 Consume one O-ring kit
        orings = StockItem.objects.create(
            part=self.oring, quantity=10, location=self.store
        )

        consumed = maintenance.consume(task, self.user, orings, Decimal(1), 'Seal')

        orings.refresh_from_db()
        self.assertEqual(orings.quantity, 9)
        self.assertEqual(consumed.action, MaintenanceAction.Action.CONSUME)
        self.assertEqual(consumed.part, self.oring)
        self.assertTrue(
            StockItemTracking.objects.filter(
                item=orings, deltas__maintenance=task.pk
            ).exists()
        )

        # The rest of the checklist
        task.checklist.filter(result=ChecklistResult.Result.PENDING).update(
            result=ChecklistResult.Result.OK
        )

        # 5.6 Verify (fresh data from the mock platform)
        self.report_fresh(dep)
        result = maintenance.verify(task, user=self.user)

        self.assertTrue(result['passed'])
        task.refresh_from_db()
        self.assertTrue(task.verification['passed'])
        self.assertIsInstance(task.verification['checked_at'], str)

        # 5.7 Complete
        self.teams.reset_mock()

        maintenance.complete(
            task, user=self.user, labour_minutes=90, summary='Battery replaced'
        )

        task.refresh_from_db()
        dep.refresh_from_db()
        geofence.refresh_from_db()

        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.completed_by, self.user)
        self.assertEqual(task.labour_minutes, 90)

        self.assertEqual(geofence.status, AlertStatus.RESOLVED.value)
        self.assertEqual(geofence.resolution, Alert.Resolution.TASK)
        self.assertEqual(geofence.task, task)
        self.assertIn(geofence, task.alerts.all())

        # The next PM moves forward: 180 days after the completion
        self.assertGreater(dep.next_pm_date, old_pm)
        self.assertEqual(dep.next_pm_date, current_date() + timedelta(days=180))

        self.assertTrue(
            StockItemTracking.objects.filter(
                item=dep.device,
                tracking_type=StockHistoryCode.FLEET_MAINTENANCE.value,
                deltas__maintenance=task.pk,
            ).exists()
        )

        # One Teams message for the completion, listing the replacement
        self.assertEqual(self.teams.call_count, 1)
        card = self.teams.call_args.kwargs['json']['attachments'][0]['content']
        self.assertIn('completed', card['body'][0]['text'])
        self.assertIn('Algarve #1', card['body'][0]['text'])
        self.assertIn('BAT-0331', card['body'][1]['text'])
        self.assertIn('BAT-0347', card['body'][1]['text'])

        # No new proposal while the next PM is beyond the horizon
        fleet_daily_planning()
        self.assertEqual(MaintenanceTask.objects.filter(deployment=dep).count(), 1)


class Scenario6Test(MaintenanceTestBase):
    """Scenario 6: remove a component without a replacement."""

    def test_scenario_6_remove_disables_stream(self):
        """The stream the device no longer reports is disabled, and its alert closed."""
        dep = self.deploy('AUR-0170', site=self.site)
        stream = DataStream.objects.get(deployment=dep, key='battery')

        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:STREAM_MISSING:battery',
            Alert.AlertType.STREAM_MISSING,
            AlertSeverity.WARNING.value,
            'No data from stream Battery',
            deployment=dep,
            site=self.site,
            stream=stream,
        )

        task = self.started_task(dep, TaskType.CORRECTIVE)
        battery = self.installed(dep)

        action = maintenance.component_action(
            task,
            self.user,
            {
                'action': 'remove',
                'component': battery.pk,
                'quantity': Decimal(1),
                'disposition': 'keep',
                'notes': '',
                'disable_stream': stream,
            },
        )

        battery.refresh_from_db()
        stream.refresh_from_db()
        alert.refresh_from_db()

        self.assertEqual(action.action, MaintenanceAction.Action.REMOVE)
        self.assertIsNone(battery.belongs_to)
        self.assertEqual(battery.location, self.workshop)

        self.assertFalse(stream.enabled)
        self.assertFalse(stream.essential)
        self.assertEqual(action.metadata['disabled_stream'], 'battery')

        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.TASK)

        # The next poll does not raise it again
        self.report_fresh(dep)
        MockDataPlatformClient.statuses[dep.device.fleet_link.platform_id].streams = [
            StreamStatus(key='hydrophone', last_seen=timezone.now())
        ]
        monitoring.poll_all(force=True)

        self.assertFalse(
            Alert.objects
            .filter(stream=stream)
            .exclude(status=AlertStatus.RESOLVED.value)
            .exists()
        )

    def test_disable_stream_rules(self):
        """Only a removal can disable a stream, and only one of the deployment."""
        dep = self.deploy('AUR-0171', site=self.site)
        other = self.deploy('AUR-0172')
        task = self.started_task(dep, TaskType.CORRECTIVE)

        battery = self.installed(dep)
        data = {
            'action': 'remove',
            'component': battery.pk,
            'quantity': Decimal(1),
            'notes': '',
            'disable_stream': DataStream.objects.get(deployment=other, key='battery'),
        }

        with self.assertRaises(ValidationError):
            maintenance.component_action(task, self.user, data)

        data['action'] = 'add'

        with self.assertRaises(ValidationError):
            maintenance.component_action(task, self.user, data)


class Scenario8Test(MaintenanceTestBase):
    """Scenario 8: NO_SERVICE coverage gets no PM proposals and no PM alerts."""

    def test_scenario_8_no_service(self):
        """Monitored only: no proposals, no PM alerts."""
        site = Site.objects.create(name='Remote #1', coverage=Coverage.NO_SERVICE)
        dep = self.deploy('AUR-0180', site=site)

        self.assertEqual(dep.coverage, Coverage.NO_SERVICE)
        self.assertIsNone(dep.next_pm_date)

        # Even with a PM date set by hand
        dep.next_pm_date = current_date() - timedelta(days=3)
        dep.next_pm_manual = True
        dep.save()

        fleet_daily_planning()

        self.assertFalse(MaintenanceTask.objects.filter(deployment=dep).exists())
        self.assertFalse(
            Alert.objects.filter(
                deployment=dep, alert_type__in=planning.PM_ALERT_TYPES
            ).exists()
        )

    def test_coverage_change_cancels_proposal(self):
        """A proposal is cancelled when the deployment stops getting PM."""
        dep = self.deploy('AUR-0181', site=self.site)
        task = planning.propose_pm_tasks()[0]

        dep.coverage = Coverage.NO_SERVICE
        dep.save()

        self.assertEqual(planning.cancel_stale_proposals(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)


class PlanningTest(MaintenanceTestBase):
    """PM proposals and planning alerts."""

    def test_proposals(self):
        """One proposal per deployment, without a site, following the PM date."""
        dep = self.deploy('AUR-0190')  # no site, no position
        far = self.deploy('AUR-0191', site=self.site, days_ago=10)

        self.assertIsNone(dep.site)

        created = planning.propose_pm_tasks()
        self.assertEqual([task.deployment for task in created], [dep])

        task = created[0]
        self.assertIsNone(task.site)
        self.assertEqual(maintenance.task_name(task), 'AUR-0190')
        self.assertEqual(planning.task_device_name(dep), 'AUR-0190')

        # Idempotent
        self.assertEqual(planning.propose_pm_tasks(), [])

        # The due date of a proposal follows the PM date
        dep.next_pm_date = current_date() + timedelta(days=20)
        dep.next_pm_manual = True
        dep.save()

        planning.propose_pm_tasks()
        task.refresh_from_db()
        self.assertEqual(task.due_date, dep.next_pm_date)

        # A site override shortens the interval
        self.assertFalse(MaintenanceTask.objects.filter(deployment=far).exists())

        self.site.pm_interval_override_days = 30
        self.site.save()
        far.refresh_from_db()
        deployment_service.compute_next_pm(far)

        self.assertEqual(len(planning.propose_pm_tasks()), 1)

    def test_recovered_cancels_proposal(self):
        """A proposal whose deployment ended is cancelled."""
        dep = self.deploy('AUR-0192', site=self.site)
        task = planning.propose_pm_tasks()[0]

        deployment_service.recover(dep, user=self.user)

        summary = planning.run_daily_planning()
        self.assertEqual(summary['cancelled'], 1)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)

    def test_pm_alerts(self):
        """PM_DUE within the warning window, PM_OVERDUE once past due."""
        dep = self.deploy('AUR-0193', site=self.site)
        dep.next_pm_manual = True

        def alerts(alert_type):
            return Alert.objects.filter(deployment=dep, alert_type=alert_type)

        # Outside the warning window: nothing
        dep.next_pm_date = current_date() + timedelta(days=20)
        dep.save()
        planning.pm_alerts()
        self.assertFalse(Alert.objects.filter(deployment=dep).exists())

        # Due soon
        dep.next_pm_date = current_date() + timedelta(days=5)
        dep.save()
        planning.pm_alerts()

        due = alerts(Alert.AlertType.PM_DUE).get()
        self.assertEqual(due.severity, AlertSeverity.INFO.value)
        self.assertEqual(due.status, AlertStatus.OPEN.value)
        self.assertIn('Algarve #1', due.message)

        # Overdue: the PM_DUE alert clears
        dep.next_pm_date = current_date() - timedelta(days=1)
        dep.save()
        planning.pm_alerts()

        due.refresh_from_db()
        self.assertEqual(due.status, AlertStatus.RESOLVED.value)

        overdue = alerts(Alert.AlertType.PM_OVERDUE).get()
        self.assertEqual(overdue.severity, AlertSeverity.WARNING.value)

        # Not notified (not critical)
        self.assertEqual(self.teams.call_count, 0)

        # A completed preventive task clears it
        task = self.started_task(dep)
        self.answer_checklist(task)
        self.report_fresh(dep)
        maintenance.verify(task)
        dep.next_pm_manual = False
        dep.save()
        maintenance.complete(task, user=self.user)

        overdue.refresh_from_db()
        self.assertEqual(overdue.status, AlertStatus.RESOLVED.value)
        self.assertEqual(overdue.resolution, Alert.Resolution.TASK)

        planning.pm_alerts()
        self.assertFalse(
            Alert.objects
            .filter(deployment=dep)
            .exclude(status=AlertStatus.RESOLVED.value)
            .exists()
        )

    def test_pipeline_alerts(self):
        """Readiness risks become alerts; BUILD_LATE is notified once."""
        dep = pipeline.create_planned(
            self.device_type,
            site=self.site,
            target_date=current_date() + timedelta(days=60),
        )
        build = pipeline.create_build_for(dep)
        build.target_date = dep.target_date + timedelta(days=5)
        build.save()

        summary = planning.pipeline_alerts()
        self.assertEqual(summary['notified'], 1)

        alert = Alert.objects.get(deployment=dep, alert_type=Alert.AlertType.BUILD_LATE)
        self.assertEqual(alert.severity, AlertSeverity.WARNING.value)
        self.assertEqual(alert.dedupe_key, f'dp:{dep.pk}:BUILD_LATE')
        self.assertEqual(self.teams.call_count, 1)

        # Again: no duplicate, no second notification
        self.assertEqual(planning.pipeline_alerts()['notified'], 0)
        self.assertEqual(
            Alert.objects.filter(alert_type=Alert.AlertType.BUILD_LATE).count(), 1
        )
        self.assertEqual(self.teams.call_count, 1)

        # Fixed: the alert clears
        build.target_date = dep.target_date - timedelta(days=10)
        build.save()

        planning.pipeline_alerts()
        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.AUTO)

    def test_unscheduled_alert_not_notified(self):
        """NO_DEPLOY_DATE is raised (INFO) but not notified."""
        dep = pipeline.create_planned(self.device_type)
        Deployment.objects.filter(pk=dep.pk).update(
            creation_date=current_date() - timedelta(days=30)
        )

        planning.pipeline_alerts()

        alert = Alert.objects.get(deployment=dep)
        self.assertEqual(alert.alert_type, Alert.AlertType.NO_DEPLOY_DATE)
        self.assertEqual(alert.severity, AlertSeverity.INFO.value)
        self.assertEqual(self.teams.call_count, 0)

    def test_failing_step_is_isolated(self):
        """A failing planning step does not stop the others."""
        self.deploy('AUR-0194', site=self.site)

        with mock.patch(
            'fleet.services.planning.pm_alerts', side_effect=RuntimeError('boom')
        ):
            summary = planning.run_daily_planning()

        self.assertIsNone(summary['pm_alerts'])
        self.assertEqual(summary['proposed'], 1)


class TaskLifecycleTest(MaintenanceTestBase):
    """Task rules: start, checklist, verification, follow-up, cancel."""

    def test_start_rules(self):
        """Only proposed or scheduled tasks start; the checklist is made once."""
        dep = self.deploy('AUR-0200', site=self.site)
        task = self.started_task(dep, TaskType.CORRECTIVE)

        self.assertEqual(
            list(task.checklist.values_list('text', flat=True)),
            ['Inspect O-rings', 'Find the root cause'],
        )

        with self.assertRaises(ValidationError):
            maintenance.start(task)

        # Actions need a started task
        idle = maintenance.create_task(dep.device, TaskType.INSPECTION)

        with self.assertRaises(ValidationError):
            maintenance.record_action(idle, self.user, MaintenanceAction.Action.CLEAN)

    def test_create_task_rules(self):
        """The deployment defaults to the active one, and must match the device."""
        dep = self.deploy('AUR-0201', site=self.site)
        other = self.deploy('AUR-0202')

        task = maintenance.create_task(dep.device)
        self.assertEqual(task.deployment, dep)
        self.assertEqual(task.site, self.site)
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)

        task = maintenance.create_task(
            dep.device, scheduled_date=current_date() + timedelta(days=1)
        )
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)

        with self.assertRaises(ValidationError):
            maintenance.create_task(dep.device, deployment=other)

        orings = StockItem.objects.create(
            part=self.oring, quantity=5, location=self.store
        )

        with self.assertRaises(ValidationError):
            maintenance.create_task(orings)

    def test_complete_rules(self):
        """Required checklist items, then verification or an override reason."""
        dep = self.deploy('AUR-0203', site=self.site)
        task = self.started_task(dep)

        # Required items are pending
        with self.assertRaises(ValidationError):
            maintenance.complete(task, user=self.user)

        task.checklist.filter(required=True).update(result=ChecklistResult.Result.NA)

        # Not verified, no override reason
        with self.assertRaises(ValidationError):
            maintenance.complete(task, user=self.user)

        # The device has stopped reporting: the data check fails
        platform_id = dep.device.fleet_link.platform_id
        MockDataPlatformClient.statuses[platform_id] = DeviceStatus(
            platform_id=platform_id, streams=[], position=None
        )

        self.assertFalse(maintenance.verify(task)['passed'])

        maintenance.complete(task, user=self.user, override_reason='No signal on site')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertEqual(task.verification_override_reason, 'No signal on site')

        follow_up = MaintenanceTask.objects.get(follow_up_of=task)
        self.assertEqual(follow_up.task_type, TaskType.CORRECTIVE)
        self.assertEqual(follow_up.status, TaskStatus.PROPOSED.value)
        self.assertEqual(follow_up.deployment, dep)
        self.assertIn('No signal on site', follow_up.description)

        with self.assertRaises(ValidationError):
            maintenance.cancel(task)

    def test_workshop_task(self):
        """A task on a unit in stock needs no verification."""
        device = self.make_device('AUR-0204', battery='BAT-0400')

        task = maintenance.create_task(device, TaskType.INSPECTION)
        self.assertIsNone(task.deployment)
        self.assertEqual(maintenance.task_name(task), 'AUR-0204')

        maintenance.start(task, user=self.user)

        # The device type comes from the device part
        self.assertEqual(maintenance.get_device_type(task), self.device_type)
        self.answer_checklist(task)

        with self.assertRaises(ValidationError):
            maintenance.verify(task)

        with self.assertRaises(ValidationError):
            maintenance.reposition(task, self.user, 37.1, -7.8)

        maintenance.record_action(
            task,
            self.user,
            MaintenanceAction.Action.FIRMWARE,
            note='Updated',
            firmware_version='2.4.1',
        )
        self.assertEqual(
            DeviceLink.objects.get(stock_item=device).firmware_version, '2.4.1'
        )

        # Removed into the workshop (no trip)
        battery = StockItem.objects.get(serial='BAT-0400')
        maintenance.component_action(
            task,
            self.user,
            {'action': 'destroy', 'component': battery.pk, 'quantity': Decimal(1)},
        )

        battery.refresh_from_db()
        self.assertEqual(battery.status, StockStatus.DESTROYED.value)

        maintenance.complete(task, user=self.user, summary='Bench check')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertFalse(MaintenanceTask.objects.filter(follow_up_of=task).exists())

    def test_reposition(self):
        """Repositioning moves the nominal position and keeps the old one."""
        dep = self.deploy('AUR-0205', site=self.site)
        task = self.started_task(dep, TaskType.CORRECTIVE)

        action = maintenance.reposition(
            task, self.user, Decimal('37.010000'), Decimal('-7.910000'), 'Moved'
        )

        dep.refresh_from_db()
        self.assertEqual(dep.latitude, Decimal('37.010000'))
        self.assertEqual(action.metadata['old_position']['latitude'], 37.0)
        self.assertEqual(action.metadata['new_position']['longitude'], -7.91)

    def test_consume_rules(self):
        """Consumables: bulk stock only, within the available quantity."""
        dep = self.deploy('AUR-0206', site=self.site)
        task = self.started_task(dep, TaskType.CORRECTIVE)

        orings = StockItem.objects.create(
            part=self.oring, quantity=2, location=self.store
        )

        with self.assertRaises(ValidationError):
            maintenance.consume(task, self.user, orings, Decimal(3))

        with self.assertRaises(ValidationError):
            maintenance.consume(task, self.user, dep.device, Decimal(1))

        maintenance.consume(task, self.user, orings, Decimal(2))
        self.assertEqual(MaintenanceAction.objects.get(task=task).quantity, Decimal(2))

    def test_create_from_alert(self):
        """A corrective task for an alert, which is acknowledged."""
        dep = self.deploy('AUR-0207', site=self.site)

        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.CRITICAL.value,
            'No data received from the device',
            deployment=dep,
            site=self.site,
        )

        task = maintenance.create_from_alert(alert, user=self.user)

        alert.refresh_from_db()
        self.assertEqual(task.task_type, TaskType.CORRECTIVE)
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)
        self.assertEqual(task.description, 'No data received from the device')
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED.value)
        self.assertEqual(alert.task, task)
        self.assertIn(alert, task.alerts.all())

        with self.assertRaises(ValidationError):
            maintenance.create_from_alert(alert)

        # Completing it resolves the alert, even without a passed data check
        maintenance.start(task, user=self.user)
        self.answer_checklist(task)
        maintenance.complete(task, user=self.user, override_reason='Checked by hand')

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.TASK)

    def test_cancel(self):
        """An open task can be cancelled."""
        dep = self.deploy('AUR-0208', site=self.site)
        task = maintenance.create_task(dep.device)

        maintenance.cancel(task, reason='Duplicate')
        task.refresh_from_db()

        self.assertEqual(task.status, TaskStatus.CANCELLED.value)
        self.assertIn('Duplicate', task.summary)

    def test_report(self):
        """The service report template installs and renders a task."""
        from fleet.management.commands.fleet_install_reports import install_reports
        from report.models import ReportTemplate

        dep = self.deploy('AUR-0209', site=self.site)
        task = self.started_task(dep)
        self.answer_checklist(task)
        maintenance.record_action(
            task, self.user, MaintenanceAction.Action.CLEAN, note='Biofouling'
        )

        self.assertIn('Created: Fleet Service Report', install_reports())
        self.assertEqual(install_reports()[0][:5], 'Kept:')
        self.assertEqual(install_reports(update=True)[0][:8], 'Updated:')

        template = ReportTemplate.objects.get(model_type='maintenancetask')
        html = template.render_as_string(task)

        self.assertIn(task.reference, html)
        self.assertIn('Algarve #1', html)
        self.assertIn('Measure battery voltage', html)
        self.assertIn('Biofouling', html)


class MaintenanceAPITest(MaintenanceDataMixin, InvenTreeAPITestCase):
    """The maintenance endpoints (P3)."""

    roles = ['fleet.view', 'fleet.add', 'fleet.change', 'fleet.delete', 'stock.change']

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_fleet_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def task_url(self, task, action=None):
        """Return the URL of a task, or of one of its actions."""
        if action:
            return reverse(f'api-fleet-task-{action}', kwargs={'pk': task.pk})

        return reverse('api-fleet-task-detail', kwargs={'pk': task.pk})

    def test_task_flow(self):
        """Create, schedule, start, execute and complete a task over the API."""
        dep = self.deploy('AUR-0300')  # no site
        url = reverse('api-fleet-task-list')

        response = self.post(
            url,
            {
                'device': dep.device.pk,
                'task_type': 'CORRECTIVE',
                'description': 'Check the mooring',
            },
            expected_code=201,
        )

        task = MaintenanceTask.objects.get(pk=response.data['pk'])
        self.assertEqual(response.data['status'], TaskStatus.PROPOSED.value)
        self.assertEqual(response.data['deployment'], dep.pk)
        self.assertEqual(response.data['display_name'], 'AUR-0300')
        self.assertTrue(response.data['reference'].startswith('MT-'))

        # Scheduling by date
        response = self.patch(
            self.task_url(task),
            {'scheduled_date': (current_date() + timedelta(days=2)).isoformat()},
        )
        self.assertEqual(response.data['status'], TaskStatus.SCHEDULED.value)

        # The checklist cannot be filled before the start
        response = self.post(self.task_url(task, 'start'), {}, expected_code=200)
        self.assertEqual(response.data['status'], TaskStatus.IN_PROGRESS.value)
        self.assertEqual(response.data['checklist_count'], 2)
        self.assertEqual(response.data['checklist_pending'], 2)
        self.assertIn(
            self.user.username,
            [user['username'] for user in response.data['technicians_detail']],
        )

        # The device and type are fixed once started
        self.patch(self.task_url(task), {'task_type': 'PREVENTIVE'}, expected_code=400)

        checklist = self.get(
            reverse('api-fleet-checklist-result-list'), {'task': task.pk}
        ).data
        self.assertEqual(len(checklist), 2)

        for row in checklist:
            self.patch(
                reverse('api-fleet-checklist-result-detail', kwargs={'pk': row['pk']}),
                {'result': 'OK', 'fault_code': self.fault.pk, 'note': 'fine'},
            )

        # Component action (replace)
        old = self.installed(dep)
        new = StockItem.objects.create(
            part=self.battery, quantity=1, serial='BAT-0347', location=self.store
        )

        response = self.post(
            self.task_url(task, 'component-action'),
            {
                'action': 'replace',
                'component': old.pk,
                'stock_item': new.pk,
                'disposition': 'damaged',
                'checklist_result': checklist[0]['pk'],
            },
            expected_code=200,
        )
        self.assertEqual(response.data['action_count'], 1)

        old.refresh_from_db()
        self.assertEqual(old.location, self.workshop)

        # Missing inputs
        self.post(
            self.task_url(task, 'component-action'),
            {'action': 'replace', 'component': old.pk},
            expected_code=400,
        )

        # Consume, record action, reposition
        orings = StockItem.objects.create(
            part=self.oring, quantity=4, location=self.store
        )
        self.post(
            self.task_url(task, 'consume'),
            {'stock_item': orings.pk, 'quantity': 2},
            expected_code=200,
        )
        self.post(
            self.task_url(task, 'record-action'),
            {'action': 'FIRMWARE', 'firmware_version': '3.0', 'note': 'OTA'},
            expected_code=200,
        )
        self.post(
            self.task_url(task, 'reposition'),
            {'latitude': '37.1', 'longitude': '-7.8'},
            expected_code=200,
        )

        actions = self.get(reverse('api-fleet-task-action-list'), {'task': task.pk})
        self.assertEqual(
            [row['action'] for row in actions.data],
            ['REPLACE', 'CONSUME', 'FIRMWARE', 'REPOSITION'],
        )
        self.assertEqual(actions.data[0]['component_in_detail']['serial'], 'BAT-0347')

        # Verify, then complete
        self.post(
            self.task_url(task, 'complete'), {'summary': 'Done'}, expected_code=400
        )

        self.report_fresh(dep)
        response = self.post(self.task_url(task, 'verify'), {}, expected_code=200)
        self.assertTrue(response.data['verification']['passed'])

        response = self.post(
            self.task_url(task, 'complete'),
            {'summary': 'Done', 'labour_minutes': 45},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], TaskStatus.COMPLETED.value)

        # The checklist is locked after completion
        self.patch(
            reverse(
                'api-fleet-checklist-result-detail', kwargs={'pk': checklist[0]['pk']}
            ),
            {'result': 'ISSUE'},
            expected_code=400,
        )

        # Completed tasks cannot be deleted
        self.delete(self.task_url(task), expected_code=400)

    def test_filters_and_overview(self):
        """Task filters, the deployment PM filter and the overview counts."""
        dep = self.deploy('AUR-0301', site=self.site)
        mine = maintenance.create_task(
            dep.device, due_date=current_date() - timedelta(days=1)
        )
        mine.technicians.add(self.user)
        other = maintenance.create_task(dep.device, TaskType.INSPECTION)
        maintenance.cancel(other)

        url = reverse('api-fleet-task-list')

        def pks(params):
            return [row['pk'] for row in self.get(url, params).data]

        self.assertEqual(pks({'assigned_to_me': True}), [mine.pk])
        self.assertEqual(pks({'open': True}), [mine.pk])
        self.assertEqual(pks({'open': False}), [other.pk])
        self.assertEqual(pks({'overdue': True}), [mine.pk])
        self.assertEqual(
            pks({'deployment': dep.pk, 'ordering': 'reference'}), [mine.pk, other.pk]
        )
        self.assertEqual(pks({'status': TaskStatus.CANCELLED.value}), [other.pk])
        self.assertEqual(pks({'search': 'AUR-0301'}), [other.pk, mine.pk])

        # Calendar range: the scheduled date, else the due date
        yesterday = (current_date() - timedelta(days=1)).isoformat()
        self.assertEqual(pks({'min_date': yesterday, 'max_date': yesterday}), [mine.pk])
        self.assertEqual(pks({'min_date': current_date().isoformat()}), [])

        response = self.get(reverse('api-fleet-overview'))
        self.assertEqual(response.data['tasks_open'], 1)
        self.assertEqual(response.data['tasks_overdue'], 1)
        self.assertEqual(response.data['tasks_in_progress'], 0)

        dep.next_pm_date = current_date() - timedelta(days=1)
        dep.next_pm_manual = True
        dep.save()

        response = self.get(reverse('api-fleet-deployment-list'), {'pm_overdue': True})
        self.assertEqual([row['pk'] for row in response.data], [dep.pk])

        # Calendar: the open task (due date)
        response = self.get(
            reverse('api-fleet-calendar'),
            {
                'start': (current_date() - timedelta(days=5)).isoformat(),
                'end': (current_date() + timedelta(days=5)).isoformat(),
            },
        )
        self.assertIn(
            mine.reference, [event['title'].split(' ')[0] for event in response.data]
        )

    def test_alert_create_task(self):
        """Create a corrective task from an alert."""
        dep = self.deploy('AUR-0302', site=self.site)
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.CRITICAL.value,
            'No data',
            deployment=dep,
            site=self.site,
        )

        url = reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk})

        response = self.post(
            url,
            {'scheduled_date': (current_date() + timedelta(days=1)).isoformat()},
            expected_code=201,
        )

        self.assertEqual(response.data['task_type'], 'CORRECTIVE')
        self.assertEqual(response.data['status'], TaskStatus.SCHEDULED.value)
        self.assertEqual(response.data['alerts'], [alert.pk])

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED.value)

        self.post(url, {}, expected_code=400)

    def test_templates_and_fault_codes(self):
        """Checklist and kit templates per device type, and fault codes."""
        url = reverse('api-fleet-checklist-item-list')

        response = self.post(
            url,
            {
                'device_type': self.device_type.pk,
                'text': 'Clean the hydrophone',
                'sequence': 5,
                'task_type': '',
            },
            expected_code=201,
        )
        self.assertEqual(response.data['task_type'], '')

        response = self.get(url, {'device_type': self.device_type.pk})
        self.assertEqual(len(response.data), 5)
        self.assertEqual(response.data[-1]['text'], 'Clean the hydrophone')

        response = self.get(url, {'task_type': 'CORRECTIVE'})
        self.assertEqual(len(response.data), 1)

        url = reverse('api-fleet-kit-line-list')
        response = self.post(
            url,
            {
                'device_type': self.device_type.pk,
                'part': self.battery.pk,
                'quantity': 1,
                'mode': 'LIKELY',
            },
            expected_code=201,
        )
        self.assertEqual(response.data['part_detail']['name'], 'Battery pack')

        self.post(
            url,
            {'device_type': self.device_type.pk, 'part': self.oring.pk, 'quantity': -1},
            expected_code=400,
        )

        response = self.get(
            reverse('api-fleet-device-type-detail', kwargs={'pk': self.device_type.pk})
        )
        self.assertEqual(response.data['checklist_count'], 5)
        self.assertEqual(response.data['kit_line_count'], 2)

        response = self.get(reverse('api-fleet-fault-code-list'), {'active': True})
        self.assertIn('BATTERY_DEGRADATION', [row['code'] for row in response.data])

    def test_schema(self):
        """The maintenance endpoints publish a valid OpenAPI schema."""
        schema = SchemaGenerator(
            patterns=[path('api/fleet/', include(fleet_api_urls))]
        ).get_schema(public=True)

        validate_schema(schema)

        for url in [
            '/api/fleet/task/{id}/start/',
            '/api/fleet/task/{id}/component-action/',
            '/api/fleet/task/{id}/complete/',
            '/api/fleet/alert/{id}/create-task/',
        ]:
            self.assertIn('post', schema['paths'][url])

        self.assertIn('get', schema['paths']['/api/fleet/task/checklist/'])
        self.assertIn('get', schema['paths']['/api/fleet/fault-code/'])

    def test_options(self):
        """The forms of the maintenance endpoints load (OPTIONS metadata)."""
        dep = self.deploy('AUR-0303', site=self.site)
        task = maintenance.create_task(dep.device)
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.WARNING.value,
            'No data',
            deployment=dep,
        )

        urls = [
            self.task_url(task, action)
            for action in [
                'start',
                'component-action',
                'consume',
                'reposition',
                'record-action',
                'verify',
                'complete',
                'cancel',
            ]
        ]
        urls += [
            self.task_url(task),
            reverse('api-fleet-task-list'),
            reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk}),
            reverse('api-fleet-checklist-item-list'),
            reverse('api-fleet-kit-line-list'),
            reverse('api-fleet-fault-code-list'),
        ]

        for url in urls:
            self.options(url, expected_code=200)

        actions = self.options(
            self.task_url(task, 'component-action'), expected_code=200
        ).data['actions']['POST']

        self.assertEqual(actions['fault_code']['model'], 'faultcode')
        self.assertEqual(
            actions['checklist_result']['api_url'],
            reverse('api-fleet-checklist-result-list'),
        )
        self.assertEqual(
            actions['disable_stream']['api_url'], reverse('api-fleet-stream-list')
        )
        self.assertFalse(actions['location']['required'])


class MaintenancePermissionTest(MaintenanceDataMixin, InvenTreeAPITestCase):
    """Scenario 9: permissions on the maintenance endpoints."""

    roles = []

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_fleet_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def test_scenario_9_permissions(self):
        """No role: 403. A technician executes tasks, but cannot delete sites."""
        dep = self.deploy('AUR-0400', site=self.site)
        task = maintenance.create_task(dep.device)

        for name in [
            'api-fleet-task-list',
            'api-fleet-checklist-result-list',
            'api-fleet-task-action-list',
            'api-fleet-fault-code-list',
            'api-fleet-checklist-item-list',
            'api-fleet-kit-line-list',
        ]:
            self.get(reverse(name), expected_code=403)

        start = reverse('api-fleet-task-start', kwargs={'pk': task.pk})
        self.post(start, {}, expected_code=403)

        # View only: read, but no actions
        self.assignRole('fleet.view')
        self.get(reverse('api-fleet-task-list'))
        self.post(start, {}, expected_code=403)

        # Technician: fleet change (+ stock change for component actions)
        self.assignRole('fleet.change')
        self.post(start, {}, expected_code=200)

        battery = self.installed(dep)
        action = reverse('api-fleet-task-component-action', kwargs={'pk': task.pk})
        body = {'action': 'remove', 'component': battery.pk}

        response = self.post(action, body, expected_code=403)
        self.assertIn('stock.change', str(response.data))

        self.assignRole('stock.change')
        self.post(action, body, expected_code=200)

        # Technicians cannot delete sites, or create tasks by hand
        self.delete(
            reverse('api-fleet-site-detail', kwargs={'pk': self.site.pk}),
            expected_code=403,
        )
        self.post(
            reverse('api-fleet-task-list'), {'device': dep.device.pk}, expected_code=403
        )

        # ...but can create one from an alert
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.WARNING.value,
            'No data',
            deployment=dep,
        )
        self.post(
            reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk}),
            {},
            expected_code=201,
        )

        # Checklist results can be filled in (change)
        row = ChecklistResult.objects.filter(task=task).first()
        self.patch(
            reverse('api-fleet-checklist-result-detail', kwargs={'pk': row.pk}),
            {'result': 'OK'},
        )


class DeviceStatePlanningTest(MaintenanceTestBase):
    """Internal device states in planning and maintenance (P7, section 2.11)."""

    def state(self, dep):
        """Return the effective device state of a deployment."""
        return device_state.device_state(dep)

    def test_docked_not_planned(self):
        """A docked device gets no PM proposals or PM alerts."""
        dep = self.deploy('AUR-0800')  # PM due in 10 days, no site
        task = planning.propose_pm_tasks()[0]

        device_state.set_state(dep, InternalState.DOCKED, user=self.user)

        # The proposal is cancelled at once
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)

        dep.refresh_from_db()
        dep.next_pm_date = current_date() - timedelta(days=3)
        dep.next_pm_manual = True
        dep.save()

        summary = planning.run_daily_planning()
        self.assertEqual(summary['proposed'], 0)
        self.assertEqual(summary['pm_alerts'], {'raised': 0, 'resolved': 0})
        self.assertEqual(self.state(dep), 'DOCKED')

        # Undocked: planned again
        device_state.set_state(dep, InternalState.NONE, user=self.user)

        summary = planning.run_daily_planning()
        self.assertEqual(summary['proposed'], 1)
        self.assertEqual(summary['pm_alerts']['raised'], 1)
        self.assertEqual(self.state(dep), 'MAINTENANCE_OVERDUE')

    def test_docked_stale_proposal_cancelled(self):
        """A proposal of a device docked elsewhere (admin) is cancelled by the daily run."""
        dep = self.deploy('AUR-0801')
        task = planning.propose_pm_tasks()[0]

        DeviceLink.objects.filter(stock_item=dep.device).update(
            state=InternalState.DOCKED
        )

        self.assertEqual(planning.run_daily_planning()['cancelled'], 1)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)

    def test_maintenance_states(self):
        """MAINTENANCE_OVERDUE, and MAINTENANCE_SCHEDULED once a trip is planned."""
        dep = self.deploy('AUR-0802', days_ago=190)  # PM 10 days overdue
        dep.last_contact = timezone.now()
        dep.save()

        self.assertEqual(self.state(dep), 'MAINTENANCE_OVERDUE')

        # A task with only a date does not count
        task = maintenance.create_task(
            dep.device, TaskType.PREVENTIVE, scheduled_date=current_date()
        )
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)
        self.assertEqual(self.state(dep), 'MAINTENANCE_OVERDUE')

        # On a field trip: scheduled (overdue is never shown then)
        trip = trips.create_trip('Algarve run', current_date(), tasks=[task])
        self.assertEqual(self.state(dep), 'MAINTENANCE_SCHEDULED')

        # Docked wins over scheduled
        device_state.set_state(dep, InternalState.DOCKED, user=self.user)
        self.assertEqual(self.state(dep), 'DOCKED')
        device_state.set_state(dep, InternalState.NONE, user=self.user)

        task.refresh_from_db()
        trips.remove_task(trip, task)
        self.assertEqual(self.state(dep), 'MAINTENANCE_OVERDUE')

        # Overdue wins over an acknowledged problem; NO_SERVICE is never overdue
        device_state.set_state(dep, InternalState.PROBLEM_ACKNOWLEDGED, user=self.user)
        self.assertEqual(self.state(dep), 'MAINTENANCE_OVERDUE')

        dep.refresh_from_db()
        dep.coverage = Coverage.NO_SERVICE
        dep.save()
        self.assertEqual(self.state(dep), 'PROBLEM_ACKNOWLEDGED')

        device_state.set_state(dep, InternalState.NONE, user=self.user)
        self.assertEqual(self.state(dep), 'ACTIVE')

        # Pipeline deployments have no state
        planned = pipeline.create_planned(self.device_type)
        self.assertIsNone(self.state(planned))

    def test_complete_clears_problem(self):
        """Completing a task on the device clears an acknowledged problem."""
        dep = self.deploy('AUR-0803')
        device_state.set_state(
            dep, InternalState.PROBLEM_ACKNOWLEDGED, user=self.user, note='Battery'
        )

        task = self.started_task(dep, TaskType.CORRECTIVE)
        self.answer_checklist(task)
        self.report_fresh(dep)
        maintenance.verify(task)
        maintenance.complete(task, user=self.user, summary='Battery replaced')

        link = DeviceLink.objects.get(stock_item=dep.device)
        self.assertEqual(link.state, InternalState.NONE)
        self.assertIn(task.reference, link.state_note)

    def test_docked_task_without_verification(self):
        """A task on a docked device (on land) closes without "Verify data"."""
        dep = self.deploy('AUR-0804')
        device_state.set_state(dep, InternalState.DOCKED, user=self.user)

        task = self.started_task(dep, TaskType.INSPECTION)
        self.answer_checklist(task)

        self.assertFalse(maintenance.needs_verification(task))

        with self.assertRaises(ValidationError):
            maintenance.verify(task)

        maintenance.complete(task, user=self.user)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertEqual(task.verification_override_reason, '')
        self.assertFalse(MaintenanceTask.objects.filter(follow_up_of=task).exists())

        # A task keeps the device docked (only a problem is cleared)
        self.assertEqual(self.state(dep), 'DOCKED')

    def test_decommission(self):
        """Decommissioning closes the deployment and cancels the tasks not started."""
        dep = self.deploy('AUR-0805')
        task = maintenance.create_task(dep.device, TaskType.PREVENTIVE)
        trips.create_trip('Last run', current_date(), tasks=[task])

        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.WARNING.value,
            'No data',
            deployment=dep,
        )

        # Not while a task is in progress
        started = self.started_task(dep, TaskType.INSPECTION)

        with self.assertRaises(ValidationError):
            device_state.set_state(dep, InternalState.DECOMMISSIONED, user=self.user)

        maintenance.cancel(started)

        device_state.set_state(dep, InternalState.DECOMMISSIONED, user=self.user)

        dep.refresh_from_db()
        dep.device.refresh_from_db()
        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertIsNotNone(dep.device.customer)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CANCELLED.value)
        self.assertIsNone(task.trip)
        self.assertFalse(task.kit_lines.exists())

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)

        self.assertEqual(self.state(dep), 'DECOMMISSIONED')

        # A decommissioned unit cannot be put back into the pipeline
        StockItem.objects.filter(pk=dep.device.pk).update(customer=None)
        dep.device.refresh_from_db()

        planned = pipeline.create_planned(self.device_type)

        with self.assertRaises(ValidationError):
            pipeline.assign_existing_device(planned, dep.device)

    def test_decommission_pipeline_refused(self):
        """A device in a pipeline deployment cannot be decommissioned."""
        device = self.make_device('AUR-0806')
        planned = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(planned, device)

        with self.assertRaises(ValidationError):
            device_state.set_state(planned, InternalState.DECOMMISSIONED)

        with self.assertRaises(ValidationError):
            device_state.set_state(planned, 'NOT_A_STATE')


class DeviceStateAPITest(MaintenanceDataMixin, InvenTreeAPITestCase):
    """The device state endpoint, fields, filters, overview and map (P7)."""

    roles = ['fleet.view', 'fleet.change']

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_fleet_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def set_state(self, dep, state, note='', expected_code=200):
        """POST to the set-state action of a deployment."""
        return self.post(
            reverse('api-fleet-deployment-set-state', kwargs={'pk': dep.pk}),
            {'state': state, 'note': note},
            expected_code=expected_code,
        )

    def test_set_state(self):
        """Technicians (fleet change) set and clear the state."""
        dep = self.deploy('AUR-0900', site=self.site)

        response = self.set_state(dep, 'DOCKED', note='On the quay')
        self.assertEqual(response.data['device_state'], 'DOCKED')
        self.assertEqual(response.data['internal_state'], 'DOCKED')
        self.assertEqual(response.data['internal_state_note'], 'On the quay')
        self.assertEqual(
            response.data['internal_state_changed_by_detail']['username'],
            self.user.username,
        )
        self.assertIsNotNone(response.data['internal_state_changed_at'])

        response = self.get(reverse('api-fleet-device-link-list'), {'state': 'DOCKED'})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['state'], 'DOCKED')

        response = self.set_state(dep, '')
        self.assertNotEqual(response.data['device_state'], 'DOCKED')
        self.assertEqual(response.data['internal_state'], '')

        # Invalid input
        self.set_state(dep, 'IGNORE', expected_code=400)

        planned = pipeline.create_planned(self.device_type)
        self.set_state(planned, 'DOCKED', expected_code=400)

        # The device link state is read-only (set through the action)
        link = DeviceLink.objects.get(stock_item=dep.device)
        self.patch(
            reverse('api-fleet-device-link-detail', kwargs={'pk': link.pk}),
            {'state': 'DECOMMISSIONED'},
        )
        link.refresh_from_db()
        self.assertEqual(link.state, '')

        self.options(
            reverse('api-fleet-deployment-set-state', kwargs={'pk': dep.pk}),
            expected_code=200,
        )

    def test_permissions(self):
        """View-only users cannot set a state."""
        dep = self.deploy('AUR-0901')

        self.clearRoles()
        self.assignRole('fleet.view')

        self.set_state(dep, 'DOCKED', expected_code=403)

    def test_lists_overview_map(self):
        """Filters, overview counts and the map honour the states."""
        docked = self.deploy('AUR-0902', site=self.site)
        active = self.deploy('AUR-0903', days_ago=10)
        gone = self.deploy('AUR-0904', days_ago=10)

        Deployment.objects.filter(pk__in=[active.pk, gone.pk]).update(
            last_contact=timezone.now()
        )

        self.set_state(docked, 'DOCKED')
        self.set_state(gone, 'DECOMMISSIONED')

        url = reverse('api-fleet-deployment-list')

        def pks(params):
            return sorted(row['pk'] for row in self.get(url, params).data)

        self.assertEqual(pks({'device_state': 'DOCKED'}), [docked.pk])
        self.assertEqual(pks({'device_state': 'ACTIVE'}), [active.pk])
        self.assertEqual(pks({'decommissioned': True}), [gone.pk])
        self.assertEqual(pks({'active': True}), sorted([docked.pk, active.pk]))
        self.assertEqual(pks({'decommissioned': False}), sorted([docked.pk, active.pk]))

        rows = {row['pk']: row for row in self.get(url).data}
        self.assertEqual(rows[gone.pk]['device_state'], 'DECOMMISSIONED')
        self.assertEqual(rows[gone.pk]['status'], DeploymentStatus.RECOVERED.value)

        response = self.get(url, {'ordering': 'device_state'})
        self.assertEqual(len(response.data), 3)

        overview = self.get(reverse('api-fleet-overview')).data
        self.assertEqual(overview['deployed'], 1)
        self.assertEqual(overview['no_site'], 1)
        self.assertEqual(overview['state_docked'], 1)
        self.assertEqual(overview['state_active'], 1)
        self.assertEqual(overview['state_decommissioned'], 1)
        self.assertEqual(overview['state_unresponsive'], 0)
        self.assertEqual(
            overview['health_ok']
            + overview['health_degraded']
            + overview['health_critical']
            + overview['health_unknown'],
            1,
        )

        rows = self.get(reverse('api-fleet-deployment-map')).data
        self.assertEqual([row['pk'] for row in rows], [active.pk])
        self.assertEqual(rows[0]['device_state'], 'ACTIVE')
