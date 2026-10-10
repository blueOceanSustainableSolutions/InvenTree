"""Tests for the InvenTree conventions of the fleet app.

State transitions on the models (with can_* properties), events, model
validation, the global search, output options, exports and query counts.
"""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from fleet import api as fleet_api
from fleet.events import FleetEvents
from fleet.models import Deployment, MaintenanceTask, TaskType
from fleet.services import maintenance, pipeline, trips
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DeploymentStatus,
    TaskStatus,
    TripStatus,
)
from fleet.test_maintenance import MaintenanceDataMixin, MaintenanceTestBase
from generic.states import StateTransitionMixin
from InvenTree.unit_test import InvenTreeAPITestCase
from users.models import Owner


def fired(trigger, event) -> list:
    """The ids for which an event was triggered (trigger is a mock)."""
    return [
        call.kwargs.get('id')
        for call in trigger.call_args_list
        if call.args[0] == event
    ]


class TransitionTest(MaintenanceTestBase):
    """State transitions go through the model, and trigger events."""

    def ready_deployment(self, serial):
        """A READY deployment of a new unit."""
        dep = pipeline.create_planned(self.device_type, site=self.site)
        pipeline.assign_existing_device(dep, self.make_device(serial))
        dep.refresh_from_db()
        return dep

    def test_deployment_transitions(self):
        """Deploy and recover through the model methods."""
        dep = self.ready_deployment('AUR-0900')

        self.assertTrue(dep.can_deploy)
        self.assertTrue(dep.can_schedule)
        self.assertFalse(dep.can_recover)

        with (
            mock.patch('plugin.events.trigger_event') as trigger,
            mock.patch.object(
                Deployment,
                'handle_transition',
                autospec=True,
                side_effect=StateTransitionMixin.handle_transition,
            ) as transition,
        ):
            dep.deploy(user=self.user)
            dep.refresh_from_db()

            self.assertEqual(dep.status, DeploymentStatus.DEPLOYED.value)
            self.assertTrue(dep.can_recover)
            self.assertFalse(dep.can_deploy)

            dep.recover(user=self.user, notes='End of campaign')
            dep.refresh_from_db()

        self.assertEqual(dep.status, DeploymentStatus.RECOVERED.value)
        self.assertEqual(transition.call_count, 2)
        self.assertEqual(fired(trigger, FleetEvents.DEPLOYMENT_DEPLOYED), [dep.pk])
        self.assertEqual(fired(trigger, FleetEvents.DEPLOYMENT_RECOVERED), [dep.pk])

        # A recovered deployment cannot be deployed again
        with self.assertRaises(ValidationError):
            dep.deploy(user=self.user)

    def test_task_transitions(self):
        """Start, complete and cancel through the model methods."""
        dep = self.deploy('AUR-0901', site=self.site)
        task = maintenance.create_task(dep.device, task_type=TaskType.INSPECTION)

        self.assertTrue(task.can_start)
        self.assertFalse(task.can_complete)

        with mock.patch('plugin.events.trigger_event') as trigger:
            task.start_task(user=self.user)
            self.assertTrue(task.can_complete)

            task.checklist.update(result='OK')
            task.complete_task(user=self.user, override_reason='No data link on site')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED.value)
        self.assertFalse(task.can_cancel)
        self.assertEqual(fired(trigger, FleetEvents.TASK_STARTED), [task.pk])
        self.assertEqual(fired(trigger, FleetEvents.TASK_COMPLETED), [task.pk])

        other = maintenance.create_task(dep.device, task_type=TaskType.CORRECTIVE)

        with mock.patch('plugin.events.trigger_event') as trigger:
            other.cancel_task(user=self.user, reason='Not needed')

        other.refresh_from_db()
        self.assertEqual(other.status, TaskStatus.CANCELLED.value)
        self.assertEqual(fired(trigger, FleetEvents.TASK_CANCELLED), [other.pk])

        with self.assertRaises(ValidationError):
            other.start_task(user=self.user)

    def test_trip_transitions(self):
        """Start and cancel a trip through the model methods."""
        trip = trips.create_trip(title='Algarve', start_date=timezone.now().date())

        self.assertTrue(trip.can_start)
        self.assertTrue(trip.can_cancel)
        self.assertFalse(trip.can_reconcile)

        with mock.patch('plugin.events.trigger_event') as trigger:
            trip.start_trip(user=self.user)

        trip.refresh_from_db()
        self.assertEqual(trip.status, TripStatus.IN_PROGRESS.value)
        self.assertFalse(trip.can_cancel)
        self.assertTrue(trip.can_reconcile)
        self.assertEqual(fired(trigger, FleetEvents.TRIP_STARTED), [trip.pk])

        # Starting a trip makes the user responsible (as an owner)
        self.assertEqual(trip.responsible, Owner.get_owner(self.user))

        other = trips.create_trip(title='Sagres', start_date=timezone.now().date())

        with mock.patch('plugin.events.trigger_event') as trigger:
            other.cancel_trip(user=self.user, reason='Weather')

        other.refresh_from_db()
        self.assertEqual(other.status, TripStatus.CANCELLED.value)
        self.assertEqual(fired(trigger, FleetEvents.TRIP_CANCELLED), [other.pk])

    def test_alert_transitions(self):
        """Acknowledge and resolve an alert through the model methods."""
        dep = self.deploy('AUR-0902', site=self.site)

        from fleet.services import monitoring

        with mock.patch('plugin.events.trigger_event') as trigger:
            alert, _event = monitoring.open_or_update_alert(
                'test:1',
                'NO_CONTACT',
                AlertSeverity.WARNING.value,
                'No contact',
                deployment=dep,
            )

            self.assertTrue(alert.can_acknowledge)
            alert.acknowledge(user=self.user)
            self.assertFalse(alert.can_acknowledge)
            self.assertTrue(alert.can_resolve)

            alert.resolve(user=self.user, note='Fixed')

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, alert.Resolution.MANUAL)
        self.assertFalse(alert.can_resolve)

        for event in [
            FleetEvents.ALERT_OPENED,
            FleetEvents.ALERT_ACKNOWLEDGED,
            FleetEvents.ALERT_RESOLVED,
        ]:
            self.assertEqual(fired(trigger, event), [alert.pk])

    def test_severity_escalation(self):
        """Severities are ordered: WARNING -> CRITICAL is an escalation."""
        from fleet.services import monitoring

        dep = self.deploy('AUR-0903', site=self.site)

        monitoring.open_or_update_alert(
            'test:2', 'NO_CONTACT', AlertSeverity.WARNING.value, 'x', deployment=dep
        )
        _alert, event = monitoring.open_or_update_alert(
            'test:2', 'NO_CONTACT', AlertSeverity.CRITICAL.value, 'x', deployment=dep
        )
        self.assertEqual(event, 'escalated')

        _alert, event = monitoring.open_or_update_alert(
            'test:2', 'NO_CONTACT', AlertSeverity.INFO.value, 'x', deployment=dep
        )
        self.assertIsNone(event)

    def test_model_validation(self):
        """The data rules live in Model.clean()."""
        dep = self.deploy('AUR-0904', site=self.site)

        dep.replaces = dep
        dep.deployment_type = Deployment.DeploymentType.REPLACEMENT

        with self.assertRaises(ValidationError) as error:
            dep.full_clean()

        self.assertIn('replaces', error.exception.message_dict)

        dep.refresh_from_db()
        other = self.deploy('AUR-0905')
        dep.replaces = other

        with self.assertRaises(ValidationError):
            dep.full_clean()

        task = MaintenanceTask(
            device=other.device, deployment=dep, task_type=TaskType.INSPECTION
        )

        with self.assertRaises(ValidationError) as error:
            task.clean()

        self.assertIn('deployment', error.exception.message_dict)

        trip = trips.create_trip(title='Algarve', start_date=timezone.now().date())
        trip.end_date = trip.start_date - timedelta(days=1)

        with self.assertRaises(ValidationError) as error:
            trip.full_clean()

        self.assertIn('end_date', error.exception.message_dict)

    def test_barcodes(self):
        """Deployments, tasks and trips have their own barcode codes."""
        from plugin.base.barcodes.helper import get_supported_barcode_model_codes_map

        codes = get_supported_barcode_model_codes_map()

        self.assertEqual(codes['DP'], Deployment)
        self.assertEqual(codes['MT'], MaintenanceTask)
        self.assertEqual(codes['FT'].__name__, 'FieldTrip')


class ConventionsAPITest(MaintenanceDataMixin, InvenTreeAPITestCase):
    """Search, output options, exports, query counts and token scopes."""

    roles = ['fleet.view', 'fleet.change', 'stock.view']

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_fleet_data()

    def setUp(self):
        """Deploy a few devices, with tasks and a trip."""
        super().setUp()
        self.configure()

        self.deps = [
            self.deploy(f'AUR-10{index}', days_ago=10 + index) for index in range(4)
        ]

        for dep in self.deps:
            maintenance.create_task(dep.device, task_type=TaskType.INSPECTION)

        self.trip = trips.create_trip(
            title='Algarve', start_date=timezone.now().date(), team=[self.user]
        )

    def test_search(self):
        """Fleet references are found by the global search."""
        task = MaintenanceTask.objects.first()

        response = self.post(
            reverse('api-search'),
            {
                'search': task.reference,
                'limit': 5,
                'maintenancetask': {},
                'deployment': {},
                'fieldtrip': {},
                'site': {},
            },
            expected_code=200,
        )

        self.assertEqual(response.data['maintenancetask']['count'], 1)

        for key in ['deployment', 'fieldtrip', 'site']:
            self.assertIn(key, response.data)

    def test_output_options(self):
        """The detail fields can be switched on and off."""
        self.run_output_test(
            reverse('api-fleet-deployment-list'),
            [
                'device_type_detail',
                'build_detail',
                'device_detail',
                'site_detail',
                'client_detail',
            ],
            assert_subset=True,
        )

        self.run_output_test(
            reverse('api-fleet-task-list'),
            ['device_detail', 'deployment_detail', 'site_detail'],
            assert_subset=True,
        )

    def test_export(self):
        """The fleet lists can be exported."""
        for name in [
            'api-fleet-deployment-list',
            'api-fleet-task-list',
            'api-fleet-trip-list',
        ]:
            with self.export_data(reverse(name)) as data_file:
                self.process_csv(data_file, required_cols=['Reference'])

    def test_query_counts(self):
        """The lists do not run a query per row."""
        for name in [
            'api-fleet-deployment-list',
            'api-fleet-task-list',
            'api-fleet-trip-list',
            'api-fleet-alert-list',
        ]:
            self.get(reverse(name), max_query_count=40)

        self.get(
            reverse('api-fleet-deployment-list'), {'risks': True}, max_query_count=80
        )

    def test_trip_removed_location(self):
        """The "Removed" location of a trip comes from an annotation."""
        response = self.get(
            reverse('api-fleet-trip-detail', kwargs={'pk': self.trip.pk})
        )

        self.assertEqual(
            response.data['removed_location'], trips.get_removed_location(self.trip).pk
        )
        self.assertEqual(
            [user['username'] for user in response.data['team_detail']],
            [self.user.username],
        )

    def test_action_token_scopes(self):
        """An action which moves stock also needs the stock token scope."""
        scopes = fleet_api.DeploymentDeploy().required_alternate_scopes['POST']
        self.assertEqual(scopes, [['r:change:fleet', 'r:change:stock']])

        scopes = fleet_api.TaskStart().required_alternate_scopes['POST']
        self.assertEqual(scopes, [['r:change:fleet']])

        scopes = fleet_api.DeploymentCreateBuild().required_alternate_scopes['POST']
        self.assertEqual(scopes, [['r:add:fleet', 'r:add:build']])
