"""Tests for fleet field trips (phase P4).

Covers the acceptance scenarios 5 (scheduled maintenance with replacement,
with the trip, its kit and the reconcile), 7 (swap) and 3.1 (a ready
deployment added to a trip) from FLEET_PLAN.md section 6, plus the trips
service rules, the kit suggestion and the trip API.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import include, path, reverse

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from common.settings import set_global_setting
from fleet.api import fleet_api_urls
from fleet.models import (
    Alert,
    ChecklistResult,
    Deployment,
    DeviceLink,
    FieldTrip,
    KitMode,
    KitTemplateLine,
    MaintenanceAction,
    MaintenanceTask,
    TaskType,
    TripKitLine,
)
from fleet.services import maintenance, monitoring, pipeline, planning, trips
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DeploymentStatus,
    TaskStatus,
    TripStatus,
)
from fleet.tasks import fleet_daily_planning
from fleet.test_maintenance import MaintenanceDataMixin
from InvenTree.helpers import current_date
from InvenTree.unit_test import InvenTreeAPITestCase, InvenTreeTestCase
from stock.models import StockItem, StockItemTracking, StockLocation
from stock.status_codes import StockHistoryCode, StockStatus
from users.models import Owner


class TripDataMixin(MaintenanceDataMixin):
    """Shared data: the maintenance data, plus the kit parent location."""

    @classmethod
    def create_trip_data(cls):
        """Create the fleet data, the kit parent location and a LIKELY kit line."""
        cls.create_fleet_data()

        cls.kits = StockLocation.objects.create(name='Field Kits', structural=True)

        KitTemplateLine.objects.create(
            device_type=cls.device_type,
            part=cls.battery,
            quantity=1,
            mode=KitMode.LIKELY,
        )

        # Leftover O-rings return to the store
        cls.oring.default_location = cls.store
        cls.oring.save()

    def configure(self):
        """Configure the fleet settings (also the kit parent location)."""
        super().configure()
        set_global_setting('FLEET_KIT_PARENT_LOCATION', self.kits.pk)

    def new_trip(self, **kwargs):
        """Create a trip which starts tomorrow."""
        kwargs.setdefault('title', 'Algarve run')
        kwargs.setdefault('start_date', current_date() + timedelta(days=1))
        return trips.create_trip(**kwargs)

    def replacement(self, old, serial):
        """Plan a READY replacement of a deployed device, with a new unit."""
        dep = pipeline.create_planned(
            self.device_type,
            site=old.site,
            deployment_type=Deployment.DeploymentType.REPLACEMENT,
            replaces=old,
        )
        pipeline.assign_existing_device(dep, self.make_device(serial))
        dep.refresh_from_db()
        return dep


class TripTestBase(TripDataMixin, InvenTreeTestCase):
    """Service tests for field trips."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_trip_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def complete(self, task, dep):
        """Answer the checklist, verify (fresh data) and complete a task."""
        DeviceLink.objects.filter(stock_item=dep.device, platform_id='').update(
            platform_id=dep.device.serial
        )
        dep = Deployment.objects.get(pk=dep.pk)

        task.checklist.filter(result=ChecklistResult.Result.PENDING).update(
            result=ChecklistResult.Result.OK
        )
        self.report_fresh(dep)
        maintenance.verify(task, user=self.user)
        return maintenance.complete(task, user=self.user, summary='Done')


class Scenario5TripTest(TripTestBase):
    """Scenario 5 in full: maintenance with a replacement, on a field trip."""

    def test_scenario_5_full(self):
        """Propose, plan a trip, kit, execute, complete and reconcile."""
        dep = self.deploy('AUR-0161', site=self.site, battery='BAT-0331')
        old_pm = dep.next_pm_date

        # 5.1 The daily task proposes a PM task
        fleet_daily_planning()
        task = MaintenanceTask.objects.get(deployment=dep)
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)

        # 5.2 The manager ticks the task and creates a trip with it
        trip = self.new_trip(
            vessel='Blue I', responsible=Owner.get_owner(self.user), tasks=[task]
        )
        task.refresh_from_db()

        self.assertEqual(trip.status, TripStatus.PLANNING.value)
        self.assertTrue(trip.reference.startswith('TRIP-'))
        self.assertEqual(task.trip, trip)
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)
        self.assertEqual(task.scheduled_date, trip.start_date)

        # The kit location TRIP-xxxx (and its "Removed" child) under "Field Kits"
        kit = trip.kit_location
        self.assertEqual(kit.name, trip.reference)
        self.assertEqual(kit.parent, self.kits)
        self.assertFalse(kit.structural)
        removed = kit.children.get(name='Removed')
        self.assertFalse(removed.structural)

        # Kit suggestion: the O-ring kit (ALWAYS) and a battery (LIKELY)
        orings = StockItem.objects.create(
            part=self.oring, quantity=10, location=self.store
        )
        new_battery = StockItem.objects.create(
            part=self.battery, quantity=1, serial='BAT-0347', location=self.store
        )

        planning.suggest_kit(trip)

        lines = {line.part: line for line in TripKitLine.objects.filter(trip=trip)}
        self.assertEqual(lines[self.oring].source, TripKitLine.Source.ALWAYS)
        self.assertEqual(lines[self.oring].quantity_planned, 1)
        self.assertEqual(lines[self.battery].source, TripKitLine.Source.LIKELY)

        availability = {row['line'].part: row for row in trips.kit_lines(trip)}
        self.assertEqual(availability[self.oring]['available'], 10)
        self.assertEqual(availability[self.oring]['in_kit'], 0)
        self.assertEqual(availability[self.battery]['available'], 1)

        # 5.3 Prepare the kit: two O-ring kits and the new battery
        self.teams.reset_mock()

        moved = trips.prepare_kit(
            trip,
            [
                {'stock_item': orings, 'quantity': Decimal(2)},
                {'stock_item': new_battery},
            ],
            user=self.user,
        )

        trip.refresh_from_db()
        orings.refresh_from_db()
        new_battery.refresh_from_db()

        self.assertEqual(trip.status, TripStatus.KIT_READY.value)
        self.assertEqual(orings.quantity, 8)
        self.assertEqual(new_battery.location, kit)

        kit_orings = StockItem.objects.get(part=self.oring, location=kit)
        self.assertEqual(kit_orings.quantity, 2)
        self.assertIn(kit_orings, moved)
        self.assertTrue(
            StockItemTracking.objects.filter(
                item=new_battery, tracking_type=StockHistoryCode.STOCK_MOVE.value
            ).exists()
        )

        # "TRIP-xxxx ready" on Teams
        self.assertEqual(self.teams.call_count, 1)
        card = self.teams.call_args.kwargs['json']['attachments'][0]['content']
        self.assertIn(f'{trip.reference} ready', card['body'][0]['text'])

        availability = {row['line'].part: row for row in trips.kit_lines(trip)}
        self.assertEqual(availability[self.oring]['in_kit'], 2)
        self.assertEqual(availability[self.oring]['available'], 8)
        self.assertEqual(availability[self.battery]['missing'], 0)

        # 5.4 The trip starts, the technician starts the task
        trips.start_trip(trip, user=self.user)
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_PROGRESS.value)

        geofence, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:GEOFENCE_BREACH',
            Alert.AlertType.GEOFENCE_BREACH,
            AlertSeverity.CRITICAL.value,
            'Device is outside its geofence',
            deployment=dep,
            site=self.site,
        )

        maintenance.start(task, user=self.user)
        task.refresh_from_db()

        voltage = task.checklist.get(text='Measure battery voltage')
        voltage.result = ChecklistResult.Result.ISSUE
        voltage.fault_code = self.fault
        voltage.save()

        # Replace BAT-0331 with BAT-0347 from the kit: the old one goes to "Removed"
        old_battery = self.installed(dep)
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
                'fault_code': self.fault,
                'checklist_result': voltage,
            },
        )

        old_battery.refresh_from_db()
        new_battery.refresh_from_db()

        self.assertEqual(new_battery.belongs_to, dep.device)
        self.assertEqual(old_battery.location, removed)
        self.assertEqual(old_battery.status, StockStatus.DAMAGED.value)
        self.assertEqual(action.destination, removed)
        self.assertEqual(action.component_in, new_battery)

        for row in StockItemTracking.objects.order_by('pk')[before:]:
            self.assertEqual(row.deltas.get('maintenance'), task.pk)

        # 5.5 Consume one O-ring kit: from the trip kit only
        with self.assertRaises(ValidationError):
            maintenance.consume(task, self.user, orings, Decimal(1))

        maintenance.consume(task, self.user, kit_orings, Decimal(1), 'Seal')
        kit_orings.refresh_from_db()
        self.assertEqual(kit_orings.quantity, 1)

        # 5.6 / 5.7 Verify and complete
        self.teams.reset_mock()
        self.complete(task, dep)

        task.refresh_from_db()
        dep.refresh_from_db()
        geofence.refresh_from_db()

        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertEqual(geofence.status, AlertStatus.RESOLVED.value)
        self.assertEqual(geofence.resolution, Alert.Resolution.TASK)
        self.assertGreater(dep.next_pm_date, old_pm)
        self.assertEqual(self.teams.call_count, 1)

        # 5.8 Reconcile: the leftover O-ring returns to its default location,
        # BAT-0331 goes to the workshop (still DAMAGED), the trip is closed
        result = trips.reconcile(trip, user=self.user)

        trip.refresh_from_db()
        kit_orings.refresh_from_db()
        old_battery.refresh_from_db()

        self.assertTrue(result['closed'])
        self.assertEqual(result['returned'], 1)
        self.assertEqual(result['to_workshop'], 1)
        self.assertEqual(trip.status, TripStatus.CLOSED.value)
        self.assertIsNotNone(trip.end_date)

        self.assertEqual(kit_orings.location, self.store)
        self.assertEqual(old_battery.location, self.workshop)
        self.assertEqual(old_battery.status, StockStatus.DAMAGED.value)

        self.assertFalse(trips.kit_stock(trip).exists())
        self.assertFalse(trips.removed_stock(trip).exists())

        # The trip report
        context = trip.report_context()
        self.assertEqual(list(context['tasks']), [task])
        self.assertEqual(context['actions'].count(), 2)
        self.assertEqual(
            {row['name']: row['quantity'] for row in context['parts_used']},
            {self.battery.full_name: 1.0, self.oring.full_name: 1.0},
        )
        self.assertIn(geofence, context['alerts_resolved'])


class Scenario7SwapTest(TripTestBase):
    """Scenario 7: swap a deployed device for a new one, on a trip."""

    def test_scenario_7_swap(self):
        """The old device is recovered into "Removed", the new one is deployed."""
        old = self.deploy('AUR-0161', site=self.site)
        old_device = old.device
        new = self.replacement(old, 'AUR-0200')

        self.assertEqual(new.status, DeploymentStatus.READY.value)

        # The manager adds the ready replacement to a trip: a SWAP task
        trip = self.new_trip(deployments=[new])
        new.refresh_from_db()

        task = MaintenanceTask.objects.get(deployment=new)
        self.assertEqual(task.task_type, TaskType.SWAP)
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)
        self.assertEqual(task.trip, trip)
        self.assertEqual(task.device, new.device)
        self.assertEqual(task.site, self.site)
        self.assertEqual(new.status, DeploymentStatus.SCHEDULED.value)

        # The new device is a kit line (with its serial), kept by the suggestion
        planning.suggest_kit(trip)
        line = TripKitLine.objects.get(trip=trip, source=TripKitLine.Source.DEVICE)
        self.assertEqual(line.stock_item, new.device)
        self.assertEqual(line.task, task)

        trips.prepare_kit(trip, [{'stock_item': new.device}], user=self.user)
        trips.start_trip(trip, user=self.user)

        # The technician deploys the new device
        maintenance.start(task, user=self.user)
        record = maintenance.deploy(
            task, user=self.user, latitude='37.000100', longitude='-7.900100'
        )

        old.refresh_from_db()
        new.refresh_from_db()
        old_device.refresh_from_db()
        new_device = new.device
        new_device.refresh_from_db()
        removed = trips.get_removed_location(trip)

        self.assertEqual(old.status, DeploymentStatus.RECOVERED.value)
        self.assertIsNone(old_device.customer)
        self.assertEqual(old_device.location, removed)

        self.assertEqual(new.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(new_device.customer, self.customer)
        self.assertEqual(new.site, self.site)

        self.assertEqual(record.component_out, old_device)
        self.assertEqual(record.component_in, new_device)
        self.assertEqual(record.metadata['recovered'], old.reference)

        # The site keeps both deployments in its history
        self.assertEqual(
            set(self.site.deployments.values_list('pk', flat=True)), {old.pk, new.pk}
        )

        self.complete(task, new)
        new.refresh_from_db()
        self.assertEqual(new.next_pm_date, current_date() + timedelta(days=180))

        # Reconcile: the old device moves on to the workshop
        result = trips.reconcile(trip, user=self.user)
        old_device.refresh_from_db()

        self.assertTrue(result['closed'])
        self.assertEqual(old_device.location, self.workshop)


class TripRulesTest(TripTestBase):
    """Rules of the trips service."""

    def test_deploy_on_trip(self):
        """Scenario 3.1: a ready deployment on a trip, deployed by its task."""
        device = self.make_device('AUR-0170')
        dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(dep, device)

        trip = self.new_trip()
        trips.add_tasks(trip, deployments=[dep])

        dep.refresh_from_db()
        task = MaintenanceTask.objects.get(deployment=dep)

        self.assertEqual(dep.status, DeploymentStatus.SCHEDULED.value)
        self.assertEqual(task.task_type, TaskType.DEPLOYMENT)
        self.assertTrue(
            TripKitLine.objects.filter(
                trip=trip, stock_item=device, source=TripKitLine.Source.DEVICE
            ).exists()
        )

        # Adding it again is refused (it is no longer ready)
        with self.assertRaises(ValidationError):
            trips.add_tasks(self.new_trip(), deployments=[dep])

        trips.prepare_kit(trip, [{'stock_item': device}], user=self.user)
        trips.start_trip(trip, user=self.user)
        maintenance.start(task, user=self.user)

        # Only deployment and swap tasks deploy
        other = maintenance.create_task(
            self.deploy('AUR-0171').device, task_type=TaskType.INSPECTION
        )
        maintenance.start(other, user=self.user)

        with self.assertRaises(ValidationError):
            maintenance.deploy(other, user=self.user)

        maintenance.deploy(task, user=self.user)
        dep.refresh_from_db()
        device.refresh_from_db()

        self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
        self.assertEqual(device.customer, self.customer)
        self.assertTrue(
            StockItemTracking.objects.filter(
                item=device, tracking_type=StockHistoryCode.FLEET_DEPLOYED.value
            ).exists()
        )

        # The deployment task restarts the PM interval
        self.complete(task, dep)
        dep.refresh_from_db()
        self.assertEqual(dep.next_pm_date, current_date() + timedelta(days=180))

    def test_schedule_rules(self):
        """Which tasks can be added to (or taken off) a trip."""
        dep = self.deploy('AUR-0180', site=self.site)
        task = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)

        trip = self.new_trip(end_date=current_date() + timedelta(days=3))
        other = self.new_trip()

        # A date inside the trip is kept, other dates become the start date
        task.scheduled_date = current_date() + timedelta(days=2)
        task.save()
        trips.add_tasks(trip, tasks=[task])
        task.refresh_from_db()
        self.assertEqual(task.scheduled_date, current_date() + timedelta(days=2))

        # A task on another open trip
        with self.assertRaises(ValidationError):
            trips.add_tasks(other, tasks=[task])

        # Taken off: PROPOSED again, without a date
        trips.remove_task(trip, task)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)
        self.assertIsNone(task.trip)
        self.assertIsNone(task.scheduled_date)

        with self.assertRaises(ValidationError):
            trips.remove_task(trip, task)

        # A started task cannot be added or taken off
        trips.add_tasks(other, tasks=[task])
        maintenance.start(task, user=self.user)

        with self.assertRaises(ValidationError):
            trips.add_tasks(trip, tasks=[task])

        with self.assertRaises(ValidationError):
            trips.remove_task(other, task)

        # Nothing selected; a closed trip
        with self.assertRaises(ValidationError):
            trips.add_tasks(trip)

        trip.status = TripStatus.CLOSED.value
        trip.save()

        with self.assertRaises(ValidationError):
            trips.add_tasks(
                trip, tasks=[maintenance.create_task(dep.device, TaskType.INSPECTION)]
            )

    def test_unschedule_deployment(self):
        """A deployment taken off a trip is READY again, without its kit line."""
        dep = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(dep, self.make_device('AUR-0190'))

        trip = self.new_trip(deployments=[dep])
        task = MaintenanceTask.objects.get(deployment=dep)

        trips.remove_task(trip, task)
        dep.refresh_from_db()

        self.assertEqual(dep.status, DeploymentStatus.READY.value)
        self.assertFalse(TripKitLine.objects.filter(trip=trip).exists())

        # Added to another trip: the same task is reused
        other = self.new_trip(deployments=[dep])
        task.refresh_from_db()

        self.assertEqual(task.trip, other)
        self.assertEqual(MaintenanceTask.objects.filter(deployment=dep).count(), 1)

        # Another unit is assigned: the task and the kit line follow
        unit = self.make_device('AUR-0191')
        pipeline.assign_existing_device(dep, unit)
        task.refresh_from_db()

        self.assertEqual(task.device, unit)
        self.assertEqual(
            TripKitLine.objects.get(
                trip=other, source=TripKitLine.Source.DEVICE
            ).stock_item,
            unit,
        )

    def test_prepare_kit_rules(self):
        """Only stock which is in stock, and not already in the kit."""
        trip = self.new_trip()
        orings = StockItem.objects.create(
            part=self.oring, quantity=5, location=self.store
        )

        with self.assertRaises(ValidationError):
            trips.prepare_kit(trip, [])

        with self.assertRaises(ValidationError):
            trips.prepare_kit(trip, [{'stock_item': orings, 'quantity': Decimal(6)}])

        trips.prepare_kit(trip, [{'stock_item': orings}], user=self.user)
        orings.refresh_from_db()
        self.assertEqual(orings.location, trip.kit_location)

        with self.assertRaises(ValidationError):
            trips.prepare_kit(trip, [{'stock_item': orings}])

        # Taking more for a trip which is under way keeps its status
        trips.start_trip(trip)
        more = StockItem.objects.create(
            part=self.oring, quantity=1, location=self.store
        )
        trips.prepare_kit(trip, [{'stock_item': more}], user=self.user)
        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_PROGRESS.value)

        # A started trip cannot start again, or be cancelled
        with self.assertRaises(ValidationError):
            trips.start_trip(trip)

        with self.assertRaises(ValidationError):
            trips.cancel_trip(trip)

    def test_reconcile_rules(self):
        """Tasks in progress block; unstarted tasks are released; missing locations."""
        dep = self.deploy('AUR-0210', site=self.site)
        started = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)
        waiting = maintenance.create_task(
            self.deploy('AUR-0211').device, task_type=TaskType.CORRECTIVE
        )

        trip = self.new_trip(tasks=[started, waiting])

        # A part without a default location (the category has none either)
        spare = StockItem.objects.create(
            part=self.battery, quantity=1, serial='BAT-9', location=self.store
        )
        trips.prepare_kit(trip, [{'stock_item': spare}], user=self.user)
        trips.start_trip(trip)

        maintenance.start(started, user=self.user)

        with self.assertRaises(ValidationError):
            trips.reconcile(trip)

        maintenance.cancel(started, user=self.user, reason='Weather')

        result = trips.reconcile(trip, user=self.user)
        trip.refresh_from_db()
        waiting.refresh_from_db()

        self.assertFalse(result['closed'])
        self.assertEqual(result['released'], 1)
        self.assertEqual(len(result['missing_location']), 1)
        self.assertEqual(trip.status, TripStatus.RECONCILING.value)
        self.assertEqual(waiting.status, TaskStatus.PROPOSED.value)
        self.assertIsNone(waiting.trip)

        # A destination for the spare battery closes the trip
        result = trips.reconcile(
            trip,
            user=self.user,
            returns=[{'stock_item': spare, 'location': self.store}],
        )
        spare.refresh_from_db()

        self.assertTrue(result['closed'])
        self.assertEqual(spare.location, self.store)

        with self.assertRaises(ValidationError):
            trips.reconcile(trip)

    def test_cancel_and_delete(self):
        """A trip which has not started can be cancelled; its tasks are released."""
        dep = self.deploy('AUR-0220')
        task = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)
        trip = self.new_trip(tasks=[task])

        orings = StockItem.objects.create(
            part=self.oring, quantity=2, location=self.store
        )
        trips.prepare_kit(trip, [{'stock_item': orings}], user=self.user)

        # The kit must be returned first
        with self.assertRaises(ValidationError):
            trips.cancel_trip(trip)

        orings.move(self.store, 'back', self.user)

        trips.cancel_trip(trip, user=self.user, reason='Storm')
        trip.refresh_from_db()
        task.refresh_from_db()

        self.assertEqual(trip.status, TripStatus.CANCELLED.value)
        self.assertIn('Storm', trip.notes)
        self.assertEqual(task.status, TaskStatus.PROPOSED.value)
        self.assertIsNone(task.trip)

        # The empty kit locations are deleted with the trip
        kit = trip.kit_location
        trips.delete_kit_locations(trip)
        self.assertFalse(StockLocation.objects.filter(pk=kit.pk).exists())

    def test_kit_location_without_parent(self):
        """Without FLEET_KIT_PARENT_LOCATION the kit location is at the top level."""
        set_global_setting('FLEET_KIT_PARENT_LOCATION', '')

        trip = self.new_trip()

        self.assertIsNone(trip.kit_location.parent)
        self.assertTrue(trip.kit_location.children.filter(name='Removed').exists())

        # The maintenance service finds the same "Removed" location
        dep = self.deploy('AUR-0230')
        task = maintenance.create_task(dep.device)
        trips.add_tasks(trip, tasks=[task])
        task.refresh_from_db()

        self.assertEqual(
            maintenance.get_removed_location(task), trips.get_removed_location(trip)
        )

    def test_failure_history(self):
        """Parts which failed in two recent tasks of the type are LIKELY needed."""
        self.device_type.kit_lines.all().delete()

        for serial in ['AUR-0240', 'AUR-0241']:
            dep = self.deploy(serial)
            task = maintenance.create_task(dep.device, task_type=TaskType.CORRECTIVE)
            maintenance.start(task, user=self.user)
            maintenance.component_action(
                task,
                self.user,
                {
                    'action': 'remove',
                    'component': self.installed(dep).pk,
                    'quantity': Decimal(1),
                    'disposition': 'damaged',
                    'fault_code': self.fault,
                },
            )
            task.checklist.update(result=ChecklistResult.Result.OK)
            maintenance.complete(task, user=self.user, override_reason='Workshop')

        dep = self.deploy('AUR-0242')
        trip = self.new_trip(
            tasks=[maintenance.create_task(dep.device, TaskType.PREVENTIVE)]
        )

        planning.suggest_kit(trip)

        line = TripKitLine.objects.get(trip=trip)
        self.assertEqual(line.part, self.battery)
        self.assertEqual(line.source, TripKitLine.Source.LIKELY)
        self.assertIn('2/10', line.note)

        # Lines added by hand are kept when the kit is suggested again
        TripKitLine.objects.create(trip=trip, part=self.oring, quantity_planned=3)
        planning.suggest_kit(trip)
        self.assertEqual(TripKitLine.objects.filter(trip=trip).count(), 2)

    def test_quantities_add_up(self):
        """Two tasks of the same type need the template quantities twice."""
        tasks = [
            maintenance.create_task(self.deploy(serial).device, TaskType.PREVENTIVE)
            for serial in ['AUR-0250', 'AUR-0251']
        ]
        trip = self.new_trip(tasks=tasks)

        suggestion = planning.suggest_kit(trip, apply=False)
        self.assertFalse(TripKitLine.objects.filter(trip=trip).exists())

        quantities = {line['part']: line['quantity_planned'] for line in suggestion}
        self.assertEqual(quantities[self.oring], 2)
        self.assertEqual(quantities[self.battery], 2)

    def test_alert_task_on_trip(self):
        """A task created from an alert can go straight onto a trip."""
        dep = self.deploy('AUR-0260', site=self.site)
        trip = self.new_trip()
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.CRITICAL.value,
            'No data',
            deployment=dep,
        )

        task = maintenance.create_from_alert(alert, user=self.user, trip=trip)

        self.assertEqual(task.trip, trip)
        self.assertEqual(task.status, TaskStatus.SCHEDULED.value)
        self.assertEqual(task.scheduled_date, trip.start_date)

    def test_report(self):
        """The trip report template installs and renders a trip."""
        from fleet.management.commands.fleet_install_reports import install_reports
        from report.models import ReportTemplate

        dep = self.deploy('AUR-0270', site=self.site)
        task = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)
        trip = self.new_trip(vessel='Blue I', tasks=[task])
        maintenance.start(task, user=self.user)
        maintenance.record_action(
            task, self.user, MaintenanceAction.Action.CLEAN, note='Biofouling'
        )

        self.assertIn('Created: Fleet Trip Report', install_reports())

        template = ReportTemplate.objects.get(model_type='fieldtrip')
        html = template.render_as_string(trip)

        self.assertIn(trip.reference, html)
        self.assertIn('Blue I', html)
        self.assertIn(task.reference, html)
        self.assertIn('Algarve #1', html)
        self.assertIn('Biofouling', html)


class TripAPITest(TripDataMixin, InvenTreeAPITestCase):
    """The trip endpoints (P4)."""

    roles = ['fleet.view', 'fleet.add', 'fleet.change', 'fleet.delete', 'stock.change']

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_trip_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def trip_url(self, trip, action=None):
        """Return the URL of a trip, or of one of its actions."""
        if action:
            return reverse(f'api-fleet-trip-{action}', kwargs={'pk': trip.pk})

        return reverse('api-fleet-trip-detail', kwargs={'pk': trip.pk})

    def test_trip_flow(self):
        """Plan, kit, run and reconcile a trip over the API."""
        dep = self.deploy('AUR-0300')  # no site
        task = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)

        ready = pipeline.create_planned(self.device_type)
        pipeline.assign_existing_device(ready, self.make_device('AUR-0301'))

        start = current_date() + timedelta(days=2)

        # Create with the selected task (plan selection)
        response = self.post(
            reverse('api-fleet-trip-list'),
            {
                'title': 'South coast',
                'start_date': start.isoformat(),
                'vessel': 'Blue I',
                'team': [self.user.pk],
                'tasks': [task.pk],
            },
            expected_code=201,
        )

        trip = FieldTrip.objects.get(pk=response.data['pk'])
        self.assertEqual(response.data['status'], TripStatus.PLANNING.value)
        self.assertIsNotNone(response.data['kit_location'])
        self.assertIsNotNone(response.data['removed_location'])
        self.assertEqual(
            [user['username'] for user in response.data['team_detail']],
            [self.user.username],
        )
        self.assertNotIn('tasks', response.data)

        task.refresh_from_db()
        self.assertEqual(task.trip, trip)

        # The end date cannot be before the start
        self.patch(
            self.trip_url(trip),
            {'end_date': (start - timedelta(days=1)).isoformat()},
            expected_code=400,
        )

        # Add the ready deployment
        response = self.post(
            self.trip_url(trip, 'add-tasks'),
            {'deployments': [ready.pk]},
            expected_code=200,
        )
        self.assertEqual(response.data['task_count'], 2)
        self.assertEqual(response.data['open_task_count'], 2)

        self.post(self.trip_url(trip, 'add-tasks'), {}, expected_code=400)

        # Task filters
        response = self.get(reverse('api-fleet-task-list'), {'trip': trip.pk})
        self.assertEqual(len(response.data), 2)

        # Kit: suggest, add a line by hand, look at the availability
        self.post(self.trip_url(trip, 'suggest-kit'), {}, expected_code=200)

        response = self.post(
            reverse('api-fleet-trip-kit-line-list'),
            {
                'trip': trip.pk,
                'part': self.oring.pk,
                'quantity_planned': 2,
                'note': 'Spare',
            },
            expected_code=201,
        )
        self.assertEqual(response.data['source'], TripKitLine.Source.MANUAL)

        orings = StockItem.objects.create(
            part=self.oring, quantity=10, location=self.store
        )

        response = self.get(self.trip_url(trip, 'kit'))
        sources = sorted(row['line']['source'] for row in response.data['lines'])
        self.assertEqual(sources, ['ALWAYS', 'DEVICE', 'LIKELY', 'MANUAL'])

        device_row = next(
            row for row in response.data['lines'] if row['line']['source'] == 'DEVICE'
        )
        self.assertEqual(device_row['line']['serial'], 'AUR-0301')
        self.assertEqual(device_row['in_kit'], 0)

        # Prepare the kit
        response = self.post(
            self.trip_url(trip, 'prepare-kit'),
            {
                'items': [
                    {'stock_item': orings.pk, 'quantity': 3},
                    {'stock_item': ready.device.pk},
                ]
            },
            expected_code=200,
        )
        self.assertEqual(response.data['status'], TripStatus.KIT_READY.value)

        response = self.get(self.trip_url(trip, 'kit'))
        self.assertEqual(len(response.data['contents']), 2)

        # Start, deploy the ready device through its task
        self.post(self.trip_url(trip, 'start'), {}, expected_code=200)

        deploy_task = MaintenanceTask.objects.get(deployment=ready)
        self.post(
            reverse('api-fleet-task-start', kwargs={'pk': deploy_task.pk}),
            {},
            expected_code=200,
        )
        response = self.post(
            reverse('api-fleet-task-deploy', kwargs={'pk': deploy_task.pk}),
            {'latitude': '37.100000', 'longitude': '-8.000000', 'depth_m': '20'},
            expected_code=200,
        )
        self.assertEqual(response.data['action_count'], 1)

        ready.refresh_from_db()
        self.assertEqual(ready.status, DeploymentStatus.DEPLOYED.value)

        # The other task was not started: reconcile releases it, the kit returns
        response = self.post(
            self.trip_url(trip, 'reconcile'), {}, expected_code=400
        )  # the deploy task is in progress

        self.post(
            reverse('api-fleet-task-cancel', kwargs={'pk': deploy_task.pk}),
            {},
            expected_code=200,
        )

        response = self.post(self.trip_url(trip, 'reconcile'), {}, expected_code=200)

        self.assertTrue(response.data['reconcile']['closed'])
        self.assertEqual(response.data['reconcile']['released'], 1)
        self.assertEqual(response.data['status'], TripStatus.CLOSED.value)

        # Closed trips: no more kit changes, no delete
        self.post(self.trip_url(trip, 'suggest-kit'), {}, expected_code=400)
        self.delete(self.trip_url(trip), expected_code=400)

        # Filters
        response = self.get(reverse('api-fleet-trip-list'), {'open': True})
        self.assertEqual(len(response.data), 0)

        response = self.get(reverse('api-fleet-trip-list'), {'mine': True})
        self.assertEqual(len(response.data), 1)

        response = self.get(
            reverse('api-fleet-trip-list'), {'status': TripStatus.CLOSED.value}
        )
        self.assertEqual(len(response.data), 1)

        # The calendar shows the trip
        response = self.get(
            reverse('api-fleet-calendar'),
            {
                'start': (start - timedelta(days=3)).isoformat(),
                'end': (start + timedelta(days=3)).isoformat(),
            },
        )
        self.assertIn('fieldtrip', [event['model_type'] for event in response.data])

    def test_remove_cancel_delete(self):
        """Take tasks off, cancel and delete trips over the API."""
        dep = self.deploy('AUR-0310')
        task = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)
        trip = trips.create_trip(title='Short', start_date=current_date(), tasks=[task])

        self.post(
            self.trip_url(trip, 'remove-task'), {'task': task.pk}, expected_code=200
        )
        task.refresh_from_db()
        self.assertIsNone(task.trip)

        self.post(self.trip_url(trip, 'cancel'), {'reason': 'Storm'}, expected_code=200)

        kit = trip.kit_location.pk
        self.delete(self.trip_url(trip), expected_code=204)
        self.assertFalse(StockLocation.objects.filter(pk=kit).exists())

    def test_alert_create_task_with_trip(self):
        """Creating a task from an alert can put it on a trip."""
        dep = self.deploy('AUR-0320', site=self.site)
        trip = trips.create_trip(title='Alert run', start_date=current_date())
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.WARNING.value,
            'No data',
            deployment=dep,
        )

        response = self.post(
            reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk}),
            {'trip': trip.pk},
            expected_code=201,
        )

        self.assertEqual(response.data['trip'], trip.pk)
        self.assertEqual(response.data['status'], TaskStatus.SCHEDULED.value)

    def test_schema(self):
        """The trip endpoints publish a valid OpenAPI schema."""
        schema = SchemaGenerator(
            patterns=[path('api/fleet/', include(fleet_api_urls))]
        ).get_schema(public=True)

        validate_schema(schema)

        for url in [
            '/api/fleet/trip/{id}/add-tasks/',
            '/api/fleet/trip/{id}/prepare-kit/',
            '/api/fleet/trip/{id}/reconcile/',
            '/api/fleet/task/{id}/deploy/',
        ]:
            self.assertIn('post', schema['paths'][url])

        self.assertIn('get', schema['paths']['/api/fleet/trip/{id}/kit/'])
        self.assertIn('get', schema['paths']['/api/fleet/trip/kit-line/'])

    def test_options(self):
        """The forms of the trip endpoints load (OPTIONS metadata)."""
        dep = self.deploy('AUR-0330')
        task = maintenance.create_task(dep.device)
        trip = trips.create_trip(title='Forms', start_date=current_date())
        alert, _event = monitoring.open_or_update_alert(
            f'dp:{dep.pk}:NO_CONTACT',
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.WARNING.value,
            'No data',
            deployment=dep,
        )

        urls = [
            self.trip_url(trip, action)
            for action in [
                'add-tasks',
                'remove-task',
                'suggest-kit',
                'prepare-kit',
                'start',
                'reconcile',
                'cancel',
            ]
        ]
        urls += [
            self.trip_url(trip),
            reverse('api-fleet-trip-list'),
            reverse('api-fleet-trip-kit-line-list'),
            reverse('api-fleet-task-deploy', kwargs={'pk': task.pk}),
            reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk}),
        ]

        for url in urls:
            self.options(url, expected_code=200)

        actions = self.options(
            reverse('api-fleet-alert-create-task', kwargs={'pk': alert.pk}),
            expected_code=200,
        ).data['actions']['POST']

        self.assertEqual(actions['trip']['api_url'], reverse('api-fleet-trip-list'))
        self.assertEqual(actions['trip']['model'], 'fieldtrip')


class TripPermissionTest(TripDataMixin, InvenTreeAPITestCase):
    """Permissions on the trip endpoints."""

    roles = []

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_trip_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()

    def test_permissions(self):
        """No role: 403. Managers create trips; technicians run them."""
        trip = trips.create_trip(title='Roles', start_date=current_date())
        orings = StockItem.objects.create(
            part=self.oring, quantity=2, location=self.store
        )

        self.get(reverse('api-fleet-trip-list'), expected_code=403)
        self.get(
            reverse('api-fleet-trip-kit', kwargs={'pk': trip.pk}), expected_code=403
        )

        start = reverse('api-fleet-trip-start', kwargs={'pk': trip.pk})
        prepare = reverse('api-fleet-trip-prepare-kit', kwargs={'pk': trip.pk})
        body = {'items': [{'stock_item': orings.pk}]}

        self.assignRole('fleet.view')
        self.get(reverse('api-fleet-trip-list'))
        self.post(start, {}, expected_code=403)

        # Technician: fleet change (+ stock change to move stock)
        self.assignRole('fleet.change')

        response = self.post(prepare, body, expected_code=403)
        self.assertIn('stock.change', str(response.data))

        self.assignRole('stock.change')
        self.post(prepare, body, expected_code=200)
        self.post(start, {}, expected_code=200)

        # Only managers (fleet add) create trips
        self.post(
            reverse('api-fleet-trip-list'),
            {'title': 'Mine', 'start_date': current_date().isoformat()},
            expected_code=403,
        )

        self.assignRole('fleet.add')
        self.post(
            reverse('api-fleet-trip-list'),
            {'title': 'Mine', 'start_date': current_date().isoformat()},
            expected_code=201,
        )
