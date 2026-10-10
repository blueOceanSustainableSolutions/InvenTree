"""Live monitoring of deployed devices.

The data platform is polled for the latest data per stream and the last
position of each deployed device. InvenTree only stores the resulting state
(last seen per stream, health, last position, a downsampled track) and the
alerts raised from it.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import structlog

from common.settings import get_global_setting, set_global_setting
from fleet import geo, notify
from fleet.events import trigger_status_event
from fleet.helpers import normalize_datetime
from fleet.integrations.base import (
    DataPlatformClient,
    DataPlatformError,
    DeviceStatus,
    get_client,
)
from fleet.models import Alert, DataStream, Deployment, PositionFix
from fleet.services import device_state
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    AlertStatusGroups,
    DataStreamStatus,
    DeploymentStatus,
    HealthStatus,
)

logger = structlog.get_logger('inventree')

# Hidden global setting which holds the poll state (shared by all workers)
POLL_STATE_KEY = '_FLEET_POLL_STATE'

# Consecutive failed polls before the "platform unreachable" notification
FAILURES_BEFORE_NOTIFY = 3

# Alert types raised and cleared by the monitoring service
MONITORING_ALERT_TYPES = [
    Alert.AlertType.STREAM_LATE,
    Alert.AlertType.STREAM_MISSING,
    Alert.AlertType.NO_CONTACT,
    Alert.AlertType.GEOFENCE_BREACH,
]


# Position track downsampling: store a fix after this long, or this far
FIX_MIN_INTERVAL = timedelta(minutes=60)
FIX_MIN_DISTANCE_M = 25

# Position fixes older than this are pruned by the daily task
POSITION_RETENTION_DAYS = 730


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def alert_link(alert: Alert) -> str:
    """Return the notification link for an alert (its deployment page)."""
    if alert.deployment:
        return notify.build_link(
            f'deployment/{alert.deployment.pk}', obj=alert.deployment
        )

    if alert.site:
        return notify.build_link(f'sites/{alert.site.pk}', obj=alert.site)

    return notify.build_link('alerts')


def alert_facts(alert: Alert) -> list[tuple]:
    """Return the facts shown in an alert notification."""
    deployment = alert.deployment
    site = alert.site or (deployment.site if deployment else None)

    return [
        (_('Alert'), alert.reference),
        (_('Severity'), alert.get_severity_display()),
        (_('Site'), site.name if site else None),
        (_('Deployment'), deployment.reference if deployment else None),
        (
            _('Device'),
            deployment.device.serial if deployment and deployment.device else None,
        ),
    ]


def notify_alert(alert: Alert, event_code: str, title: str) -> None:
    """Send a notification about an alert."""
    notify.notify(
        event_code,
        title,
        alert.message,
        link=alert_link(alert),
        severity=alert.severity,
        obj=alert,
        facts=alert_facts(alert),
    )


def alert_title(alert: Alert, prefix: str = '') -> str:
    """Return a short title for an alert notification."""
    site = alert.site or (alert.deployment.site if alert.deployment else None)
    title = f'{prefix}{alert.get_alert_type_display()}'

    if site:
        title += f' - {site.name}'

    return title


def open_or_update_alert(
    dedupe_key: str,
    alert_type: str,
    severity: int,
    message: str,
    deployment: Deployment | None = None,
    site=None,
    stream: DataStream | None = None,
    data: dict | None = None,
) -> tuple[Alert, str | None]:
    """Open an alert for a condition, or update the unresolved one.

    A new CRITICAL alert is notified, and so is an escalation to CRITICAL,
    unless the device is PROBLEM_ACKNOWLEDGED (notifications muted).

    Returns:
        (alert, event): event is 'opened', 'escalated' or None
    """
    alert = (
        Alert.objects
        .filter(dedupe_key=dedupe_key)
        .exclude(status=AlertStatus.RESOLVED.value)
        .first()
    )

    if alert is None:
        try:
            with transaction.atomic():
                alert = Alert.objects.create(
                    dedupe_key=dedupe_key,
                    alert_type=alert_type,
                    severity=severity,
                    message=message[:500],
                    deployment=deployment,
                    site=site,
                    stream=stream,
                    data=data,
                )
        except IntegrityError:
            # Opened at the same time by another worker
            alert = (
                Alert.objects
                .filter(dedupe_key=dedupe_key)
                .exclude(status=AlertStatus.RESOLVED.value)
                .first()
            )

            if alert is None:
                raise

            return alert, None

        trigger_status_event(alert)

        if severity == AlertSeverity.CRITICAL.value and not device_state.is_muted(
            deployment
        ):
            notify_alert(alert, 'alert_opened', alert_title(alert))

        return alert, 'opened'

    # Severities are ordered: a higher value is more severe
    escalated = int(severity) > int(alert.severity)

    changed = (
        alert.severity != severity
        or alert.message != message[:500]
        or alert.data != data
        or alert.stream_id != (stream.pk if stream else None)
    )

    if changed:
        alert.severity = severity
        alert.message = message[:500]
        alert.data = data
        alert.stream = stream
        alert.save()

    if (
        escalated
        and severity == AlertSeverity.CRITICAL.value
        and not device_state.is_muted(alert.deployment)
    ):
        notify_alert(alert, 'alert_escalated', alert_title(alert))

    return alert, 'escalated' if escalated else None


def resolve_alert(
    alert: Alert,
    resolution: str = Alert.Resolution.MANUAL,
    user=None,
    note: str = '',
    task=None,
) -> Alert:
    """Resolve an alert.

    A CRITICAL alert which clears by itself (AUTO) is notified as resolved,
    unless the device is PROBLEM_ACKNOWLEDGED (notifications muted).
    """
    if not alert.can_resolve:
        raise ValidationError(_('This alert is already resolved'))

    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = timezone.now()
    alert.resolved_by = user
    alert.resolution = resolution

    if task is not None:
        alert.task = task

    if note:
        alert.data = {**(alert.data or {}), 'resolution_note': note}

    alert.save()
    trigger_status_event(alert)

    if (
        resolution == Alert.Resolution.AUTO
        and alert.severity == AlertSeverity.CRITICAL.value
        and not device_state.is_muted(alert.deployment)
    ):
        notify.notify(
            'alert_resolved',
            alert_title(alert, prefix=str(_('Resolved')) + ': '),
            alert.message,
            link=alert_link(alert),
            severity=AlertSeverity.INFO.value,
            obj=alert,
            facts=alert_facts(alert),
        )

    return alert


def acknowledge_alert(alert: Alert, user=None) -> Alert:
    """Acknowledge an open alert (someone is on it)."""
    if not alert.can_resolve:
        raise ValidationError(_('This alert is already resolved'))

    if alert.can_acknowledge:
        alert.status = AlertStatus.ACKNOWLEDGED.value
        alert.acknowledged_at = timezone.now()
        alert.acknowledged_by = user
        alert.save()
        trigger_status_event(alert)

    return alert


# ---------------------------------------------------------------------------
# Device state
# ---------------------------------------------------------------------------


def get_geofence(deployment: Deployment) -> dict:
    """Return the nominal position and geofence of a deployment.

    The nominal position falls back to the site, and the radius to the site
    and then to FLEET_GEOFENCE_DEFAULT_RADIUS_M.
    """
    site = deployment.site
    latitude = deployment.latitude
    longitude = deployment.longitude

    if (latitude is None or longitude is None) and site is not None:
        latitude, longitude = site.latitude, site.longitude

    radius = deployment.geofence_radius_m

    if radius is None and site is not None:
        radius = site.geofence_radius_m

    if radius is None:
        radius = (
            get_global_setting('FLEET_GEOFENCE_DEFAULT_RADIUS_M', cache=False) or None
        )

    polygon = deployment.geofence_polygon

    if polygon is None and site is not None:
        polygon = site.geofence_polygon

    return {
        'latitude': latitude,
        'longitude': longitude,
        'radius_m': radius,
        'polygon': polygon or None,
    }


def stream_state(stream: DataStream, deployed_at, now, missing_factor: int) -> str:
    """Return the state of a stream from the age of its last data.

    A stream which has never reported is aged from the deployment date, so a
    device which never sends data is still flagged. It stays UNKNOWN until it
    is overdue.
    """
    since = stream.last_seen or deployed_at

    if since is None:
        return DataStreamStatus.UNKNOWN.value

    age = (now - since).total_seconds() / 60
    expected = stream.expected_interval_minutes

    if age > expected * missing_factor:
        return DataStreamStatus.MISSING.value

    if age > expected + stream.grace_minutes:
        return DataStreamStatus.LATE.value

    if stream.last_seen is None:
        return DataStreamStatus.UNKNOWN.value

    return DataStreamStatus.OK.value


def format_age(delta: timedelta) -> str:
    """Return a short human readable age (e.g. '3 h 20 min')."""
    minutes = int(delta.total_seconds() // 60)

    if minutes < 60:
        return f'{minutes} min'

    hours, minutes = divmod(minutes, 60)

    if hours < 48:
        return f'{hours} h {minutes} min' if minutes else f'{hours} h'

    return f'{hours // 24} d'


def to_decimal(value, places: int) -> Decimal | None:
    """Round a float to a Decimal for a model field."""
    if value is None:
        return None

    return Decimal(str(round(float(value), places)))


def store_position_fix(deployment: Deployment, position) -> PositionFix | None:
    """Store a position in the track, downsampled.

    A fix is stored if it is the first one, or at least FIX_MIN_INTERVAL after
    the last stored fix, or more than FIX_MIN_DISTANCE_M away from it.
    """
    timestamp = normalize_datetime(position.timestamp)
    last = deployment.positions.order_by('-timestamp').first()

    if last is not None:
        if timestamp <= last.timestamp:
            return None

        moved = geo.haversine(
            last.latitude, last.longitude, position.latitude, position.longitude
        )

        if (
            timestamp - last.timestamp < FIX_MIN_INTERVAL
            and moved <= FIX_MIN_DISTANCE_M
        ):
            return None

    return PositionFix.objects.create(
        deployment=deployment,
        timestamp=timestamp,
        latitude=to_decimal(position.latitude, 6),
        longitude=to_decimal(position.longitude, 6),
        source=position.source or 'platform',
    )


def apply_status(
    deployment: Deployment, status: DeviceStatus | None, now: datetime | None = None
) -> Deployment:
    """Apply a data platform status to a deployment, and raise or clear alerts.

    Arguments:
        deployment: A DEPLOYED deployment
        status: The reported status, or None if the platform returned nothing
            for this device (the streams then age without new data)
        now: Current time (for tests)
    """
    now = now or timezone.now()
    previous_health = deployment.health
    missing_factor = max(
        1, get_global_setting('FLEET_STREAM_MISSING_FACTOR', cache=False)
    )
    no_contact_hours = max(1, get_global_setting('FLEET_NO_CONTACT_HOURS', cache=False))
    warn_percent = get_global_setting('FLEET_GEOFENCE_WARN_PERCENT', cache=False)

    reported = {}

    if status is not None:
        for item in status.streams:
            if item.last_seen is not None:
                last_seen = normalize_datetime(item.last_seen)
                reported[item.key] = max(last_seen, reported.get(item.key, last_seen))

    # 1. Streams
    streams = list(deployment.streams.all())

    for stream in streams:
        last_seen = reported.get(stream.key)
        fields = []

        if last_seen is not None and (
            stream.last_seen is None or last_seen > stream.last_seen
        ):
            stream.last_seen = last_seen
            fields.append('last_seen')

        state = (
            stream_state(stream, deployment.deployed_at, now, missing_factor)
            if stream.enabled
            else DataStreamStatus.UNKNOWN.value
        )

        if state != stream.state:
            stream.state = state
            fields.append('state')

        if fields:
            stream.save(update_fields=fields)

    # 2. Contact: the latest data of any stream (known to InvenTree or not)
    seen = [s.last_seen for s in streams if s.last_seen] + list(reported.values())

    if deployment.last_contact:
        seen.append(deployment.last_contact)

    deployment.last_contact = max(seen) if seen else None

    contact_since = deployment.last_contact or deployment.deployed_at
    no_contact = contact_since is not None and now - contact_since > timedelta(
        hours=no_contact_hours
    )

    # 3. Position
    if status is not None and status.position is not None:
        position = status.position
        timestamp = normalize_datetime(position.timestamp)

        if (
            deployment.last_position_at is None
            or timestamp >= deployment.last_position_at
        ):
            deployment.last_latitude = to_decimal(position.latitude, 6)
            deployment.last_longitude = to_decimal(position.longitude, 6)
            deployment.last_position_at = timestamp

            store_position_fix(deployment, position)

    fence = get_geofence(deployment)

    inside, distance = geo.check_geofence(
        deployment.last_latitude,
        deployment.last_longitude,
        fence['latitude'],
        fence['longitude'],
        fence['radius_m'],
        fence['polygon'],
    )

    deployment.distance_from_nominal_m = to_decimal(distance, 2)

    # 4. Health
    enabled = [s for s in streams if s.enabled]

    def has(essential: bool, state: str) -> bool:
        return any(s.essential == essential and s.state == state for s in enabled)

    near_edge = (
        not fence['polygon']
        and distance is not None
        and fence['radius_m']
        and distance > fence['radius_m'] * warn_percent / 100
    )

    if has(True, DataStreamStatus.MISSING.value) or no_contact or inside is False:
        health = HealthStatus.CRITICAL.value
    elif (
        has(True, DataStreamStatus.LATE.value)
        or has(False, DataStreamStatus.MISSING.value)
        or near_edge
    ):
        health = HealthStatus.DEGRADED.value
    elif deployment.last_contact is None:
        health = HealthStatus.UNKNOWN.value
    else:
        health = HealthStatus.OK.value

    deployment.health = health
    deployment.last_polled_at = now

    deployment.save(
        update_fields=[
            'last_contact',
            'last_latitude',
            'last_longitude',
            'last_position_at',
            'distance_from_nominal_m',
            'health',
            'last_polled_at',
        ]
    )

    # 5. Alerts
    active_keys = set()

    def raise_alert(alert_type, severity, message, suffix='', stream=None, data=None):
        key = f'dp:{deployment.pk}:{alert_type}'

        if suffix:
            key += f':{suffix}'

        active_keys.add(key)

        open_or_update_alert(
            key,
            alert_type,
            severity,
            message,
            deployment=deployment,
            site=deployment.site,
            stream=stream,
            data=data,
        )

    for stream in enabled:
        name = stream.name or stream.key
        since = stream.last_seen or deployment.deployed_at
        age = format_age(now - since) if since else ''
        data = {'stream': stream.key, 'last_seen': str(stream.last_seen or '')}

        if stream.state == DataStreamStatus.MISSING.value:
            raise_alert(
                Alert.AlertType.STREAM_MISSING,
                AlertSeverity.CRITICAL.value
                if stream.essential
                else AlertSeverity.WARNING.value,
                str(_('No data from stream')) + f' {name} ({age})',
                suffix=stream.key,
                stream=stream,
                data=data,
            )
        elif stream.state == DataStreamStatus.LATE.value and stream.essential:
            raise_alert(
                Alert.AlertType.STREAM_LATE,
                AlertSeverity.WARNING.value,
                str(_('Stream is late')) + f': {name} ({age})',
                suffix=stream.key,
                stream=stream,
                data=data,
            )

    if no_contact:
        raise_alert(
            Alert.AlertType.NO_CONTACT,
            AlertSeverity.CRITICAL.value,
            str(_('No data received from the device for'))
            + f' {format_age(now - contact_since)}',
            data={'last_contact': str(deployment.last_contact or '')},
        )

    if inside is False:
        message = str(_('Device is outside its geofence'))

        if distance is not None:
            message += f' ({round(distance)} m ' + str(_('from nominal')) + ')'

        raise_alert(
            Alert.AlertType.GEOFENCE_BREACH,
            AlertSeverity.CRITICAL.value,
            message,
            data={
                'distance_m': round(distance, 1) if distance is not None else None,
                'radius_m': fence['radius_m'],
                'latitude': float(deployment.last_latitude),
                'longitude': float(deployment.last_longitude),
            },
        )

    # Conditions which have cleared
    for alert in Alert.objects.filter(
        deployment=deployment,
        alert_type__in=MONITORING_ALERT_TYPES,
        status__in=AlertStatusGroups.OPEN,
    ).exclude(dedupe_key__in=active_keys):
        resolve_alert(alert, resolution=Alert.Resolution.AUTO)

    # An acknowledged problem ends when the device is healthy again
    if health == HealthStatus.OK.value and previous_health != HealthStatus.OK.value:
        device_state.clear_problem(
            deployment.device, str(_('Cleared: device healthy again'))
        )

    return deployment


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def get_poll_state() -> dict:
    """Return the stored poll state."""
    try:
        state = json.loads(get_global_setting(POLL_STATE_KEY, cache=False) or '{}')
    except (TypeError, ValueError):
        state = {}

    return state if isinstance(state, dict) else {}


def set_poll_state(state: dict) -> None:
    """Store the poll state."""
    set_global_setting(POLL_STATE_KEY, json.dumps(state), None)


def parse_poll_time(value) -> datetime | None:
    """Parse a time stored in the poll state."""
    if not value:
        return None

    try:
        return normalize_datetime(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def monitored_deployments():
    """Return the deployed deployments which have a platform id.

    Docked (and decommissioned) devices are not polled.
    """
    return (
        Deployment.objects
        .filter(status=DeploymentStatus.DEPLOYED.value)
        .filter(device__fleet_link__isnull=False)
        .exclude(device__fleet_link__platform_id='')
        .exclude(device_state.hidden_q())
        .select_related('site', 'device', 'device__fleet_link', 'device_type')
    )


def poll_all(
    force: bool = False,
    client: DataPlatformClient | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Poll the data platform for every monitored deployment.

    Runs every minute (scheduled task), but returns early unless
    FLEET_POLL_INTERVAL_MINUTES have passed since the last poll. Platform
    errors do not raise alerts per device: after FAILURES_BEFORE_NOTIFY
    consecutive failed polls, one "data platform unreachable" notification is
    sent.

    Returns:
        A summary dict, or None if the poll was skipped
    """
    now = now or timezone.now()
    state = get_poll_state()

    interval = timedelta(
        minutes=max(1, get_global_setting('FLEET_POLL_INTERVAL_MINUTES', cache=False))
    )
    last_poll = parse_poll_time(state.get('last_poll'))

    # Allow some jitter, as the task itself runs every minute
    if not force and last_poll and now - last_poll < interval - timedelta(seconds=30):
        return None

    deployments = {
        dep.device.fleet_link.platform_id: dep for dep in monitored_deployments()
    }

    platform_ids = list(deployments)
    summary = {'polled': 0, 'updated': 0, 'failed': False, 'error': ''}

    if platform_ids:
        client = client or get_client()

        for start in range(0, len(platform_ids), client.BATCH_SIZE):
            batch = platform_ids[start : start + client.BATCH_SIZE]

            try:
                statuses = client.get_status(batch)
            except Exception as exc:
                summary['failed'] = True
                summary['error'] = str(exc)[:250]
                logger.warning('Fleet: data platform poll failed: %s', exc)
                continue

            for platform_id in batch:
                summary['polled'] += 1

                try:
                    with transaction.atomic():
                        apply_status(
                            deployments[platform_id], statuses.get(platform_id), now
                        )
                    summary['updated'] += 1
                except Exception:
                    logger.exception(
                        'Fleet: could not apply the status of %s', platform_id
                    )

    failures = int(state.get('failures') or 0)
    notified = bool(state.get('notified'))

    if summary['failed']:
        failures += 1

        if failures >= FAILURES_BEFORE_NOTIFY and not notified:
            notify.notify(
                'platform_unreachable',
                str(_('Data platform unreachable')),
                str(_('The fleet data platform has failed'))
                + f' {failures} '
                + str(_('consecutive polls'))
                + f': {summary["error"]}',
                link=notify.build_link(''),
                severity=AlertSeverity.CRITICAL.value,
            )
            notified = True
    else:
        if notified:
            notify.notify(
                'platform_reachable',
                str(_('Data platform reachable again')),
                str(_('Fleet monitoring has resumed')),
                link=notify.build_link(''),
                severity=AlertSeverity.INFO.value,
            )

        failures = 0
        notified = False

    set_poll_state({
        'last_poll': now.isoformat(),
        'failures': failures,
        'notified': notified,
        'last_error': summary['error'],
    })

    return summary


def verify_now(
    deployment: Deployment,
    since: datetime | None = None,
    client: DataPlatformClient | None = None,
    now: datetime | None = None,
) -> dict:
    """Check that a device is reporting fresh data (maintenance "Verify data").

    Nothing is stored. A stream is fresh when its latest data is at or after
    `since` (default: now minus the device type verify window).

    Returns:
        {checked_at, since, streams: [{key, name, essential, last_seen, fresh}],
         position, inside_geofence, distance_m, passed}
    """
    now = now or timezone.now()

    if since is None:
        since = now - timedelta(minutes=deployment.device_type.verify_window_minutes)

    since = normalize_datetime(since)

    link = getattr(deployment.device, 'fleet_link', None) if deployment.device else None
    platform_id = link.platform_id if link else ''

    if not platform_id:
        raise ValidationError(
            _('The device has no platform ID, so its data cannot be checked')
        )

    client = client or get_client()

    try:
        status = client.get_status([platform_id]).get(platform_id)
    except DataPlatformError as exc:
        raise ValidationError(
            str(_('The data platform could not be reached')) + f': {exc}'
        )

    reported = {}

    if status is not None:
        for item in status.streams:
            if item.last_seen is not None:
                reported[item.key] = normalize_datetime(item.last_seen)

    streams = []

    for stream in deployment.streams.filter(enabled=True).order_by('key'):
        candidates = [
            t for t in (reported.get(stream.key), stream.last_seen) if t is not None
        ]
        last_seen = max(candidates) if candidates else None

        streams.append({
            'key': stream.key,
            'name': stream.name,
            'essential': stream.essential,
            'last_seen': last_seen,
            'fresh': last_seen is not None and last_seen >= since,
        })

    position = None

    if status is not None and status.position is not None:
        position = {
            'latitude': status.position.latitude,
            'longitude': status.position.longitude,
            'timestamp': normalize_datetime(status.position.timestamp),
        }
    elif deployment.last_latitude is not None:
        position = {
            'latitude': float(deployment.last_latitude),
            'longitude': float(deployment.last_longitude),
            'timestamp': deployment.last_position_at,
        }

    inside, distance = None, None

    if position is not None:
        fence = get_geofence(deployment)
        inside, distance = geo.check_geofence(
            position['latitude'],
            position['longitude'],
            fence['latitude'],
            fence['longitude'],
            fence['radius_m'],
            fence['polygon'],
        )

    passed = all(s['fresh'] for s in streams if s['essential']) and inside is not False

    return {
        'checked_at': now,
        'since': since,
        'streams': streams,
        'position': position,
        'inside_geofence': inside,
        'distance_m': round(distance, 1) if distance is not None else None,
        'passed': passed,
    }


def prune_positions(days: int = POSITION_RETENTION_DAYS) -> int:
    """Delete position fixes older than the retention period.

    Returns:
        The number of deleted fixes
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _counts = PositionFix.objects.filter(timestamp__lt=cutoff).delete()
    return deleted
