"""Tests for the fleet monitoring service and notifications (phase P2).

Covers acceptance scenario 4 (monitoring) from FLEET_PLAN.md section 6, and
the monitoring part of scenario 8 (NO_SERVICE coverage still raises alerts).
"""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.utils import timezone

import requests

from common.models import NotificationMessage
from common.settings import set_global_setting
from company.models import Company
from fleet import notify
from fleet.integrations.base import DeviceStatus, Position, StreamStatus
from fleet.integrations.http_client import HttpDataPlatformClient, parse_device
from fleet.integrations.mock_client import MockDataPlatformClient
from fleet.models import (
    Alert,
    Coverage,
    DataStream,
    DeviceLink,
    FleetDeviceType,
    InternalState,
    PositionFix,
    Site,
    StreamTemplate,
)
from fleet.services import deployment as deployment_service
from fleet.services import device_state, monitoring, pipeline
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    DataStreamStatus,
    HealthStatus,
)
from fleet.tasks import fleet_daily_planning, fleet_poll
from InvenTree.unit_test import InvenTreeTestCase
from part.models import Part
from stock.models import StockItem, StockLocation

# Metres per degree of latitude
M_PER_DEG = 111195.0

WEBHOOK = 'https://example.webhook.office.com/workflows/fleet'


class MonitoringTestBase(InvenTreeTestCase):
    """A deployed device with an essential and a non-essential stream."""

    roles = ['fleet.view']

    @classmethod
    def setUpTestData(cls):
        """Create the device type, site, customer and workshop."""
        super().setUpTestData()

        cls.part = Part.objects.create(
            name='Aurora PI5 75m', assembly=True, trackable=True
        )
        cls.device_type = FleetDeviceType.objects.create(part=cls.part)

        # Hydrophone: late after 15 min, missing after 60 min (factor 6)
        StreamTemplate.objects.create(
            device_type=cls.device_type,
            key='hydrophone',
            name='Hydrophone',
            essential=True,
            expected_interval_minutes=10,
            grace_minutes=5,
        )

        # Battery: late after 75 min, missing after 6 h
        StreamTemplate.objects.create(
            device_type=cls.device_type,
            key='battery',
            name='Battery',
            expected_interval_minutes=60,
        )

        cls.customer = Company.objects.create(name='BlueOasis Fleet', is_customer=True)
        cls.workshop = StockLocation.objects.create(name='Workshop')

        cls.site = Site.objects.create(
            name='Algarve #1', latitude='37.000000', longitude='-7.900000'
        )

    def setUp(self):
        """Configure the settings, the mock platform and the Teams webhook."""
        super().setUp()

        set_global_setting('FLEET_DEFAULT_CUSTOMER', self.customer.pk)
        set_global_setting('FLEET_WORKSHOP_LOCATION', self.workshop.pk)
        set_global_setting('FLEET_TEAMS_WEBHOOK_URL', WEBHOOK)
        set_global_setting('FLEET_DATA_PROVIDER', 'mock')

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

        self.now = timezone.now()
        self.dep = self.deploy('AUR-0161', self.site)

    def deploy(self, serial, site, platform_id=None, deployed_at=None):
        """Deploy a new unit at a site, two days ago by default."""
        device = StockItem.objects.create(
            part=self.part, quantity=1, serial=serial, location=self.workshop
        )

        dep = pipeline.create_planned(self.device_type, site=site)
        pipeline.assign_existing_device(dep, device)
        deployment_service.deploy(
            dep, deployed_at=deployed_at or self.now - timedelta(days=2)
        )

        DeviceLink.objects.filter(stock_item=device).update(
            platform_id=serial if platform_id is None else platform_id
        )

        dep.refresh_from_db()
        return dep

    def report(self, dep=None, position=None, at=0, **streams):
        """Set the mock status of a device.

        Arguments:
            dep: The deployment (default: self.dep)
            position: Reported position
            at: Minutes after self.now which the stream ages are relative to
            streams: Stream key -> minutes since its last data
        """
        dep = dep or self.dep
        platform_id = dep.device.fleet_link.platform_id
        base = self.now + timedelta(minutes=at)

        MockDataPlatformClient.statuses[platform_id] = DeviceStatus(
            platform_id=platform_id,
            streams=[
                StreamStatus(key=key, last_seen=base - timedelta(minutes=age))
                for key, age in streams.items()
            ],
            position=position,
        )

    def position_north(self, metres, minutes_ago=0):
        """Return a position this many metres north of the site."""
        return Position(
            latitude=37.0 + metres / M_PER_DEG,
            longitude=-7.9,
            timestamp=self.now - timedelta(minutes=minutes_ago),
        )

    def poll(self, minutes_later=0):
        """Poll (forced) at self.now + minutes_later, and refresh the deployment."""
        summary = monitoring.poll_all(
            force=True, now=self.now + timedelta(minutes=minutes_later)
        )
        self.dep.refresh_from_db()
        return summary

    def open_alerts(self, dep=None):
        """Return the unresolved alerts of a deployment."""
        return Alert.objects.filter(deployment=dep or self.dep).exclude(
            status=AlertStatus.RESOLVED.value
        )

    def stream(self, key, dep=None):
        """Return a data stream of a deployment."""
        return DataStream.objects.get(deployment=dep or self.dep, key=key)


class MonitoringScenarioTest(MonitoringTestBase):
    """Scenario 4: monitoring."""

    def test_scenario_4_monitoring(self):
        """Missing stream, deduplication, recovery and geofence breach."""
        # 4.1 The essential stream is older than interval x factor (60 min)
        self.report(hydrophone=70, battery=5)
        summary = self.poll()

        self.assertEqual(summary['polled'], 1)
        self.assertFalse(summary['failed'])

        self.assertEqual(
            self.stream('hydrophone').state, DataStreamStatus.MISSING.value
        )
        self.assertEqual(self.stream('battery').state, DataStreamStatus.OK.value)
        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)

        alerts = self.open_alerts()
        self.assertEqual(alerts.count(), 1)

        alert = alerts.first()
        self.assertEqual(alert.alert_type, Alert.AlertType.STREAM_MISSING)
        self.assertEqual(alert.severity, AlertSeverity.CRITICAL.value)
        self.assertEqual(alert.status, AlertStatus.OPEN.value)
        self.assertEqual(alert.stream, self.stream('hydrophone'))
        self.assertEqual(alert.site, self.site)
        self.assertEqual(
            alert.dedupe_key, f'dp:{self.dep.pk}:STREAM_MISSING:hydrophone'
        )

        # One Teams notification, as an Adaptive Card
        self.assertEqual(self.teams.call_count, 1)
        args, kwargs = self.teams.call_args
        self.assertEqual(args[0], WEBHOOK)
        card = kwargs['json']['attachments'][0]['content']
        self.assertEqual(card['type'], 'AdaptiveCard')
        self.assertIn('Algarve #1', card['body'][0]['text'])

        # 4.2 A second poll does not duplicate the alert (or the notification)
        self.poll(minutes_later=5)

        self.assertEqual(self.open_alerts().count(), 1)
        self.assertEqual(Alert.objects.filter(deployment=self.dep).count(), 1)
        self.assertEqual(self.teams.call_count, 1)

        # 4.3 The stream comes back: the alert is resolved automatically
        self.report(hydrophone=1, battery=5, at=10)
        self.poll(minutes_later=10)

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.RESOLVED.value)
        self.assertEqual(alert.resolution, Alert.Resolution.AUTO)
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(self.dep.health, HealthStatus.OK.value)
        self.assertEqual(self.open_alerts().count(), 0)

        # A cleared critical alert is notified as resolved
        self.assertEqual(self.teams.call_count, 2)

        # 4.4 Position 340 m away with a radius of 200 m
        self.report(hydrophone=1, battery=5, at=15, position=self.position_north(340))
        self.poll(minutes_later=15)

        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)
        self.assertAlmostEqual(float(self.dep.distance_from_nominal_m), 340, delta=1)

        breach = self.open_alerts().get()
        self.assertEqual(breach.alert_type, Alert.AlertType.GEOFENCE_BREACH)
        self.assertEqual(breach.severity, AlertSeverity.CRITICAL.value)
        self.assertEqual(breach.data['radius_m'], 200)
        self.assertEqual(self.teams.call_count, 3)


class StreamStateTest(MonitoringTestBase):
    """Stream states, health and the matching alerts."""

    def test_late_then_missing(self):
        """An essential stream goes LATE (warning), then MISSING (critical)."""
        self.report(hydrophone=20, battery=5)
        self.poll()

        self.assertEqual(self.stream('hydrophone').state, DataStreamStatus.LATE.value)
        self.assertEqual(self.dep.health, HealthStatus.DEGRADED.value)

        late = self.open_alerts().get()
        self.assertEqual(late.alert_type, Alert.AlertType.STREAM_LATE)
        self.assertEqual(late.severity, AlertSeverity.WARNING.value)

        # Warnings are not sent to Teams
        self.teams.assert_not_called()

        # No newer data: 70 minutes later the stream is 90 minutes old
        self.poll(minutes_later=70)

        late.refresh_from_db()
        self.assertEqual(late.status, AlertStatus.RESOLVED.value)

        missing = self.open_alerts().get()
        self.assertEqual(missing.alert_type, Alert.AlertType.STREAM_MISSING)
        self.assertEqual(missing.severity, AlertSeverity.CRITICAL.value)
        self.assertEqual(self.teams.call_count, 1)

    def test_non_essential_missing(self):
        """A missing non-essential stream degrades the device (warning only)."""
        self.report(hydrophone=1, battery=400)
        self.poll()

        self.assertEqual(self.stream('battery').state, DataStreamStatus.MISSING.value)
        self.assertEqual(self.dep.health, HealthStatus.DEGRADED.value)

        alert = self.open_alerts().get()
        self.assertEqual(alert.severity, AlertSeverity.WARNING.value)
        self.teams.assert_not_called()

        # A non-essential late stream raises no alert
        self.report(hydrophone=1, battery=100)
        self.poll()

        self.assertEqual(self.stream('battery').state, DataStreamStatus.LATE.value)
        self.assertEqual(self.dep.health, HealthStatus.OK.value)
        self.assertEqual(self.open_alerts().count(), 0)

    def test_escalation(self):
        """A warning which becomes critical is notified once."""
        self.report(hydrophone=1, battery=400)
        self.poll()

        alert = self.open_alerts().get()
        self.assertEqual(alert.severity, AlertSeverity.WARNING.value)

        DataStream.objects.filter(deployment=self.dep, key='battery').update(
            essential=True
        )

        self.poll()
        self.poll()

        alert.refresh_from_db()
        self.assertEqual(alert.severity, AlertSeverity.CRITICAL.value)
        self.assertEqual(self.open_alerts().count(), 1)
        self.assertEqual(self.teams.call_count, 1)

    def test_disabled_stream(self):
        """Disabled streams are not monitored, and their alerts clear."""
        self.report(hydrophone=1, battery=400)
        self.poll()
        self.assertEqual(self.open_alerts().count(), 1)

        DataStream.objects.filter(deployment=self.dep, key='battery').update(
            enabled=False
        )
        self.poll()

        self.assertEqual(self.stream('battery').state, DataStreamStatus.UNKNOWN.value)
        self.assertEqual(self.dep.health, HealthStatus.OK.value)
        self.assertEqual(self.open_alerts().count(), 0)

    def test_no_contact(self):
        """A device which never reports is MISSING on every stream, and NO_CONTACT."""
        self.poll()

        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)
        self.assertIsNone(self.dep.last_contact)

        types = set(self.open_alerts().values_list('alert_type', flat=True))
        self.assertEqual(
            types, {Alert.AlertType.STREAM_MISSING, Alert.AlertType.NO_CONTACT}
        )

        # Contact is any data, even from a stream InvenTree does not know
        self.report(hydrophone=400, battery=400, extra=1)
        self.poll()

        self.assertEqual(self.dep.last_contact, self.now - timedelta(minutes=1))
        self.assertFalse(
            self.open_alerts().filter(alert_type=Alert.AlertType.NO_CONTACT).exists()
        )

    def test_new_deployment_unknown(self):
        """A device deployed just now, without data yet, is UNKNOWN (no alerts)."""
        site = Site.objects.create(name='Algarve #2', latitude=37.1, longitude=-7.8)
        dep = self.deploy('AUR-0162', site, deployed_at=self.now)

        monitoring.poll_all(force=True, now=self.now + timedelta(minutes=5))
        dep.refresh_from_db()

        self.assertEqual(dep.health, HealthStatus.UNKNOWN.value)
        self.assertEqual(self.open_alerts(dep).count(), 0)
        self.assertEqual(
            self.stream('hydrophone', dep).state, DataStreamStatus.UNKNOWN.value
        )

    def test_last_seen_never_goes_back(self):
        """Older data than already seen does not move last_seen backwards."""
        self.report(hydrophone=1, battery=5)
        self.poll()

        self.report(hydrophone=30, battery=5)
        self.poll()

        self.assertEqual(
            self.stream('hydrophone').last_seen, self.now - timedelta(minutes=1)
        )

    def test_no_service_still_monitored(self):
        """Scenario 8 (monitoring part): NO_SERVICE deployments still raise alerts."""
        site = Site.objects.create(
            name='Azores #1',
            latitude=38.5,
            longitude=-28.6,
            coverage=Coverage.NO_SERVICE,
        )
        dep = self.deploy('AUR-0170', site)

        self.assertEqual(dep.coverage, Coverage.NO_SERVICE)
        self.assertIsNone(dep.next_pm_date)

        self.report(dep=dep, hydrophone=90, battery=5)
        self.report(hydrophone=1, battery=5)
        self.poll()

        dep.refresh_from_db()
        self.assertEqual(dep.health, HealthStatus.CRITICAL.value)
        self.assertEqual(self.open_alerts(dep).count(), 1)


class PositionTest(MonitoringTestBase):
    """Positions, track downsampling and geofences."""

    def test_near_edge_degraded(self):
        """Beyond the warning percentage of the radius, the device is degraded."""
        self.report(hydrophone=1, battery=1, position=self.position_north(180))
        self.poll()

        self.assertEqual(self.dep.health, HealthStatus.DEGRADED.value)
        self.assertEqual(self.open_alerts().count(), 0)

        set_global_setting('FLEET_GEOFENCE_WARN_PERCENT', 95)
        self.poll()
        self.assertEqual(self.dep.health, HealthStatus.OK.value)

    def test_default_radius(self):
        """Without a site radius, the default radius setting is used."""
        set_global_setting('FLEET_GEOFENCE_DEFAULT_RADIUS_M', 500)

        self.report(hydrophone=1, battery=1, position=self.position_north(340))
        self.poll()

        self.assertEqual(self.dep.health, HealthStatus.OK.value)

    def test_deployment_radius(self):
        """The deployment radius (copied from the site) is used."""
        self.dep.geofence_radius_m = 100
        self.dep.save()

        self.report(hydrophone=1, battery=1, position=self.position_north(150))
        self.poll()

        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)
        self.assertTrue(
            self
            .open_alerts()
            .filter(alert_type=Alert.AlertType.GEOFENCE_BREACH)
            .exists()
        )

    def test_polygon(self):
        """A polygon geofence replaces the radius."""
        self.dep.geofence_polygon = [
            [36.99, -7.91],
            [36.99, -7.89],
            [37.01, -7.89],
            [37.01, -7.91],
        ]
        self.dep.save()

        # 340 m north is inside the polygon (it reaches ~1.1 km north)
        self.report(hydrophone=1, battery=1, position=self.position_north(340))
        self.poll()
        self.assertEqual(self.dep.health, HealthStatus.OK.value)

        self.report(hydrophone=1, battery=1, position=self.position_north(2000))
        self.poll()
        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)

    def test_track_downsampling(self):
        """Fixes are stored hourly, or when the device moved more than 25 m."""
        self.report(hydrophone=1, battery=1, position=self.position_north(10, 70))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 1)

        # 10 minutes later and 5 m away: not stored, but the last position moves
        self.report(hydrophone=1, battery=1, position=self.position_north(15, 60))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 1)
        self.assertEqual(self.dep.last_position_at, self.now - timedelta(minutes=60))

        # Moved 40 m: stored
        self.report(hydrophone=1, battery=1, position=self.position_north(55, 55))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 2)

        # Same place, 50 min later: not stored
        self.report(hydrophone=1, battery=1, position=self.position_north(55, 5))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 2)

        # Same place, 61 min after the last stored fix: stored
        self.report(hydrophone=1, battery=1, position=self.position_north(55, -6))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 3)

        # An older position is ignored
        self.report(hydrophone=1, battery=1, position=self.position_north(500, 30))
        self.poll()
        self.assertEqual(PositionFix.objects.count(), 3)
        self.assertEqual(self.dep.last_position_at, self.now + timedelta(minutes=6))

    def test_prune(self):
        """Fixes older than two years are pruned by the daily task."""
        PositionFix.objects.create(
            deployment=self.dep,
            timestamp=self.now - timedelta(days=800),
            latitude=37,
            longitude=-7.9,
        )
        PositionFix.objects.create(
            deployment=self.dep,
            timestamp=self.now - timedelta(days=10),
            latitude=37,
            longitude=-7.9,
        )

        fleet_daily_planning()

        self.assertEqual(PositionFix.objects.count(), 1)


class PollTest(MonitoringTestBase):
    """Poll interval guard and platform errors."""

    def test_interval_guard(self):
        """The poll runs once per FLEET_POLL_INTERVAL_MINUTES."""
        self.report(hydrophone=1, battery=1)

        self.assertIsNotNone(monitoring.poll_all(now=self.now))
        self.assertIsNone(monitoring.poll_all(now=self.now + timedelta(minutes=2)))
        self.assertIsNotNone(monitoring.poll_all(now=self.now + timedelta(minutes=5)))

        set_global_setting('FLEET_POLL_INTERVAL_MINUTES', 15)
        self.assertIsNone(monitoring.poll_all(now=self.now + timedelta(minutes=10)))
        self.assertIsNotNone(monitoring.poll_all(now=self.now + timedelta(minutes=21)))

        self.assertEqual(len(MockDataPlatformClient.calls), 3)

    def test_task(self):
        """The scheduled task polls through the configured (mock) client."""
        MockDataPlatformClient.generate = True

        fleet_poll()
        self.dep.refresh_from_db()

        self.assertEqual(self.dep.health, HealthStatus.OK.value)
        self.assertIsNotNone(self.dep.last_polled_at)
        self.assertIsNotNone(self.dep.last_latitude)

    def test_unlinked_devices_skipped(self):
        """Devices without a platform id are not polled."""
        DeviceLink.objects.update(platform_id='')

        summary = monitoring.poll_all(force=True)

        self.assertEqual(summary['polled'], 0)
        self.assertEqual(MockDataPlatformClient.calls, [])

    def test_platform_unreachable(self):
        """Platform errors raise no device alerts, and one notification after 3 polls."""
        MockDataPlatformClient.error = 'Connection refused'

        for minutes in [0, 5, 10, 15, 20]:
            summary = self.poll(minutes_later=minutes)
            self.assertTrue(summary['failed'])

        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(self.dep.health, HealthStatus.UNKNOWN.value)
        self.assertEqual(self.teams.call_count, 1)

        card = self.teams.call_args.kwargs['json']['attachments'][0]['content']
        self.assertIn('unreachable', card['body'][0]['text'])
        self.assertEqual(monitoring.get_poll_state()['failures'], 5)

        # Back again: one "reachable" notification, and the counter resets
        MockDataPlatformClient.error = None
        self.report(hydrophone=1, battery=1, at=25)
        self.poll(minutes_later=25)

        self.assertEqual(self.teams.call_count, 2)
        self.assertEqual(monitoring.get_poll_state()['failures'], 0)
        self.assertEqual(self.dep.health, HealthStatus.OK.value)

    def test_one_failing_device(self):
        """An error applying one device does not stop the others."""
        site = Site.objects.create(name='Algarve #2', latitude=37.1, longitude=-7.8)
        other = self.deploy('AUR-0162', site)

        self.report(hydrophone=1, battery=1)
        self.report(dep=other, hydrophone=1, battery=1)

        original = monitoring.apply_status

        def apply_status(dep, status, now=None):
            if dep.pk == self.dep.pk:
                raise ValueError('boom')
            return original(dep, status, now)

        with mock.patch.object(monitoring, 'apply_status', apply_status):
            summary = monitoring.poll_all(force=True)

        self.assertEqual(summary['polled'], 2)
        self.assertEqual(summary['updated'], 1)

        other.refresh_from_db()
        self.assertEqual(other.health, HealthStatus.OK.value)


class AlertLifecycleTest(MonitoringTestBase):
    """Acknowledge, resolve, recover and in-app notifications."""

    def test_acknowledge_and_resolve(self):
        """An acknowledged alert still clears automatically; a resolved one is final."""
        self.report(hydrophone=90, battery=1)
        self.poll()

        alert = self.open_alerts().get()
        monitoring.acknowledge_alert(alert, user=self.user)

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED.value)
        self.assertEqual(alert.acknowledged_by, self.user)

        # Still open: polling keeps the same alert
        self.poll()
        self.assertEqual(self.open_alerts().get().pk, alert.pk)

        monitoring.resolve_alert(alert, user=self.user, note='Checked on site')

        alert.refresh_from_db()
        self.assertEqual(alert.resolution, Alert.Resolution.MANUAL)
        self.assertEqual(alert.data['resolution_note'], 'Checked on site')

        with self.assertRaises(ValidationError):
            monitoring.resolve_alert(alert)

        with self.assertRaises(ValidationError):
            monitoring.acknowledge_alert(alert)

        # The condition persists, so the next poll opens a new alert
        self.poll()
        self.assertNotEqual(self.open_alerts().get().pk, alert.pk)

    def test_recover_resolves_alerts(self):
        """Recovering the device resolves its alerts."""
        self.report(hydrophone=90, battery=1)
        self.poll()

        deployment_service.recover(self.dep)

        self.assertEqual(self.open_alerts().count(), 0)

        # Recovered devices are no longer polled
        self.assertEqual(monitoring.poll_all(force=True)['polled'], 0)

    def test_in_app_notification(self):
        """Fleet users get an InvenTree notification for a critical alert."""
        self.ensurePluginsLoaded()

        self.report(hydrophone=90, battery=1)
        self.poll()

        messages = NotificationMessage.objects.filter(
            user=self.user, category='fleet.alert_opened'
        )
        self.assertEqual(messages.count(), 1)

    def test_notification_failure_is_harmless(self):
        """A failing Teams webhook does not break monitoring."""
        self.teams.side_effect = requests.ConnectionError('down')

        self.report(hydrophone=90, battery=1)
        self.poll()

        self.assertEqual(self.open_alerts().count(), 1)
        self.assertEqual(self.dep.health, HealthStatus.CRITICAL.value)


class DeviceStateMonitoringTest(MonitoringTestBase):
    """Internal device states and monitoring (P7, section 2.11)."""

    def test_docked_not_polled(self):
        """A docked device is not polled, and its alerts are closed silently."""
        self.report(hydrophone=70, battery=5)
        self.poll()

        self.assertEqual(self.open_alerts().count(), 1)
        self.assertEqual(self.teams.call_count, 1)

        device_state.set_state(self.dep, InternalState.DOCKED, user=self.user)
        self.dep.refresh_from_db()

        self.assertEqual(self.open_alerts().count(), 0)
        self.assertEqual(self.dep.health, HealthStatus.UNKNOWN.value)
        self.assertEqual(device_state.device_state(self.dep), 'DOCKED')

        # Resolving them sent nothing, and the poll skips the device
        self.assertEqual(self.teams.call_count, 1)
        self.assertEqual(self.poll(minutes_later=5)['polled'], 0)
        self.assertEqual(self.open_alerts().count(), 0)

        # Back in the water: monitored again
        device_state.set_state(self.dep, InternalState.NONE, user=self.user)
        self.report(hydrophone=1, battery=1, at=10)
        self.assertEqual(self.poll(minutes_later=10)['polled'], 1)
        self.assertEqual(self.dep.health, HealthStatus.OK.value)

    def test_problem_acknowledged_mutes(self):
        """An acknowledged problem mutes notifications until the device is healthy."""
        self.report(hydrophone=70, battery=5)
        self.poll()

        alert = self.open_alerts().get()
        self.assertEqual(self.teams.call_count, 1)

        device_state.set_state(
            self.dep,
            InternalState.PROBLEM_ACKNOWLEDGED,
            user=self.user,
            note='Hydrophone cable, waiting for parts',
        )

        alert.refresh_from_db()
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED.value)
        self.assertEqual(device_state.device_state(self.dep), 'PROBLEM_ACKNOWLEDGED')

        # A new critical alert opens, but is not notified
        self.report(hydrophone=70, battery=5, at=5, position=self.position_north(340))
        self.poll(minutes_later=5)

        self.assertTrue(
            self
            .open_alerts()
            .filter(alert_type=Alert.AlertType.GEOFENCE_BREACH)
            .exists()
        )
        self.assertEqual(self.teams.call_count, 1)

        # Healthy again: the alerts clear (not notified) and so does the state
        self.report(
            hydrophone=1,
            battery=1,
            at=10,
            position=Position(
                latitude=37.0,
                longitude=-7.9,
                timestamp=self.now + timedelta(minutes=10),
            ),
        )
        self.poll(minutes_later=10)

        self.assertEqual(self.dep.health, HealthStatus.OK.value)
        self.assertEqual(self.open_alerts().count(), 0)
        self.assertEqual(self.teams.call_count, 1)
        self.assertEqual(
            DeviceLink.objects.get(stock_item=self.dep.device).state, InternalState.NONE
        )

        # A new problem is notified again
        self.report(hydrophone=70, battery=1, at=100)
        self.poll(minutes_later=100)
        self.assertEqual(self.teams.call_count, 2)

    def test_problem_acknowledged_needs_deployed(self):
        """Only a deployed device can be docked or have a problem acknowledged."""
        deployment_service.recover(self.dep)

        for state in [InternalState.PROBLEM_ACKNOWLEDGED, InternalState.DOCKED]:
            with self.assertRaises(ValidationError):
                device_state.set_state(self.dep, state)

    def test_unresponsive(self):
        """UNRESPONSIVE: no data for FLEET_NO_CONTACT_HOURS (automatic)."""
        # Deployed two days ago and never reported
        self.assertEqual(device_state.device_state(self.dep), 'UNRESPONSIVE')

        self.report(hydrophone=1, battery=1)
        self.poll()
        self.assertEqual(device_state.device_state(self.dep), 'ACTIVE')

        # Old data only
        self.dep.last_contact = timezone.now() - timedelta(hours=7)
        self.dep.save()
        self.assertEqual(device_state.device_state(self.dep), 'UNRESPONSIVE')

        set_global_setting('FLEET_NO_CONTACT_HOURS', 12)
        self.assertEqual(device_state.device_state(self.dep), 'ACTIVE')
        set_global_setting('FLEET_NO_CONTACT_HOURS', 6)

        # A device without a platform id is not judged
        DeviceLink.objects.update(platform_id='')
        self.assertEqual(device_state.device_state(self.dep), 'ACTIVE')


class VerifyTest(MonitoringTestBase):
    """verify_now: the maintenance "Verify data" check."""

    def test_passed(self):
        """All essential streams fresh, inside the geofence."""
        self.report(hydrophone=1, battery=400, position=self.position_north(20))

        result = monitoring.verify_now(
            self.dep, since=self.now - timedelta(minutes=10), now=self.now
        )

        self.assertTrue(result['passed'])
        self.assertTrue(result['inside_geofence'])

        streams = {s['key']: s for s in result['streams']}
        self.assertTrue(streams['hydrophone']['fresh'])
        self.assertFalse(streams['battery']['fresh'])

        # Nothing is stored
        self.dep.refresh_from_db()
        self.assertIsNone(self.dep.last_contact)
        self.assertEqual(Alert.objects.count(), 0)

    def test_failed(self):
        """A stale essential stream, or a position outside the geofence, fails."""
        self.report(hydrophone=30, battery=1)
        result = monitoring.verify_now(self.dep, now=self.now)

        # Default window: the device type verify window (30 min)
        self.assertEqual(result['since'], self.now - timedelta(minutes=30))
        self.assertTrue(result['passed'])

        result = monitoring.verify_now(
            self.dep, since=self.now - timedelta(minutes=10), now=self.now
        )
        self.assertFalse(result['passed'])
        self.assertIsNone(result['inside_geofence'])

        self.report(hydrophone=1, battery=1, position=self.position_north(340))
        result = monitoring.verify_now(
            self.dep, since=self.now - timedelta(minutes=10), now=self.now
        )
        self.assertFalse(result['inside_geofence'])
        self.assertFalse(result['passed'])

    def test_errors(self):
        """No platform id, or the platform is down."""
        MockDataPlatformClient.error = 'timeout'

        with self.assertRaises(ValidationError):
            monitoring.verify_now(self.dep)

        DeviceLink.objects.update(platform_id='')
        self.dep.refresh_from_db()

        with self.assertRaises(ValidationError):
            monitoring.verify_now(self.dep)


class HttpClientTest(MonitoringTestBase):
    """The HTTP client (against the assumed API contract)."""

    def test_parse_device(self):
        """A device of the API response is parsed."""
        status = parse_device({
            'id': 'AUR-0161',
            'streams': [
                {'key': 'hydrophone', 'last_seen': '2026-10-08T10:00:00Z'},
                {'key': 'battery', 'last_seen': None},
                {'bad': 'entry'},
            ],
            'position': {
                'latitude': 37.01,
                'longitude': -7.93,
                'timestamp': '2026-10-08T10:00:00Z',
            },
        })

        self.assertEqual(status.platform_id, 'AUR-0161')
        self.assertEqual(len(status.streams), 2)
        self.assertEqual(status.streams[0].last_seen.hour, 10)
        self.assertIsNone(status.streams[1].last_seen)
        self.assertEqual(status.position.latitude, 37.01)

        self.assertIsNone(parse_device({'streams': []}))
        self.assertIsNone(parse_device({'id': 'X', 'position': {'lat': 1}}).position)

    @mock.patch('fleet.integrations.http_client.requests.get')
    def test_get_status(self, get):
        """Batch request with a bearer token; errors become DataPlatformError."""
        get.return_value.json.return_value = {
            'devices': [{'id': 'AUR-0161', 'streams': []}]
        }

        client = HttpDataPlatformClient(
            base_url='https://data.example.com/api', token='t'
        )
        result = client.get_status(['AUR-0161', 'AUR-0162'])

        self.assertEqual(list(result), ['AUR-0161'])

        args, kwargs = get.call_args
        self.assertEqual(args[0], 'https://data.example.com/api/devices/status/')
        self.assertEqual(kwargs['params'], {'ids': 'AUR-0161,AUR-0162'})
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer t')
        self.assertEqual(kwargs['timeout'], 10)

        from fleet.integrations.base import DataPlatformError

        get.side_effect = requests.Timeout('slow')

        with self.assertRaises(DataPlatformError):
            client.get_status(['AUR-0161'])

        with self.assertRaises(DataPlatformError):
            HttpDataPlatformClient(base_url='', token='').get_status(['X'])

    def test_provider_setting(self):
        """FLEET_DATA_PROVIDER selects the client."""
        from fleet.integrations.base import get_client

        self.assertIsInstance(get_client(), MockDataPlatformClient)

        set_global_setting('FLEET_DATA_PROVIDER', 'http')
        self.assertIsInstance(get_client(), HttpDataPlatformClient)


class NotifyTest(MonitoringTestBase):
    """Notification helpers."""

    def test_card(self):
        """The Teams card has a title, facts and an Open button."""
        payload = notify.build_teams_card(
            'Title',
            'Text',
            link='https://host:8444/fleet/deployment/1',
            severity='CRITICAL',
            facts=[('Site', 'Algarve #1'), ('Empty', None)],
        )

        content = payload['attachments'][0]['content']
        self.assertEqual(content['body'][0]['color'], 'Attention')
        self.assertEqual(
            content['body'][2]['facts'], [{'title': 'Site', 'value': 'Algarve #1'}]
        )
        self.assertEqual(
            content['actions'][0]['url'], 'https://host:8444/fleet/deployment/1'
        )

    def test_link(self):
        """Links use the portal URL when set."""
        set_global_setting('FLEET_PORTAL_URL', 'https://host:8444/fleet')

        self.assertEqual(
            notify.build_link('deployment/3'), 'https://host:8444/fleet/deployment/3'
        )

        set_global_setting('FLEET_PORTAL_URL', '')
        self.assertIn('/web/fleet/deployment/', notify.build_link('x', obj=self.dep))

    def test_url_settings_not_locked(self):
        """The fleet URL settings are not tied to the site URL (P0 regression)."""
        with self.settings(SITE_URL='https://inventree.example.com'):
            set_global_setting('FLEET_TEAMS_WEBHOOK_URL', WEBHOOK + '2')
            set_global_setting('FLEET_DATA_API_URL', 'https://data.example.com/api')
            set_global_setting(
                'FLEET_PORTAL_URL', 'https://inventree.example.com:8444/fleet/'
            )

        with self.assertRaises(ValidationError):
            set_global_setting('FLEET_DATA_API_URL', 'not a url')

    def test_sent_on_commit(self):
        """A notification is only sent once the transaction commits."""
        from django.db import transaction

        with (
            mock.patch('fleet.notify.on_commit', side_effect=transaction.on_commit),
            mock.patch('fleet.notify.requests.post') as teams,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                notify.notify('test', 'Title', 'Text')
                teams.assert_not_called()

            self.assertEqual(len(callbacks), 1)
            self.assertEqual(teams.call_count, 1)

    def test_no_webhook(self):
        """Without a webhook URL nothing is posted."""
        set_global_setting('FLEET_TEAMS_WEBHOOK_URL', '')

        notify.notify('test', 'Title', 'Text')
        self.teams.assert_not_called()

    def test_email(self):
        """Emails go to FLEET_ALERT_EMAILS when email is configured."""
        set_global_setting('FLEET_ALERT_EMAILS', 'a@example.com, b@example.com')

        with (
            mock.patch(
                'InvenTree.helpers_email.is_email_configured', return_value=True
            ),
            mock.patch(
                'InvenTree.helpers_email.send_email', return_value=(True, None)
            ) as send,
        ):
            notify.notify('test', 'Title', 'Text', facts=[('Site', 'X')])

        args, kwargs = send.call_args
        self.assertEqual(args[0], '[Fleet] Title')
        self.assertEqual(args[2], ['a@example.com', 'b@example.com'])
        self.assertIn('<th align="left">Site</th>', kwargs['html_message'])
