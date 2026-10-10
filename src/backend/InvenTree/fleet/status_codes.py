"""Status codes for the fleet app."""

from django.utils.translation import gettext_lazy as _

from generic.states import ColorEnum, StatusCode


class DeploymentStatus(StatusCode):
    """Defines a set of status codes for a Deployment."""

    PLANNED = 10, _('Planned'), ColorEnum.secondary  # Planned, no build yet
    IN_PRODUCTION = 20, _('In Production'), ColorEnum.primary  # Build order issued
    READY = 30, _('Ready'), ColorEnum.info  # Device built and ready to deploy
    SCHEDULED = 40, _('Scheduled'), ColorEnum.info  # Assigned to a field trip
    DEPLOYED = 50, _('Deployed'), ColorEnum.success  # Device is in the water
    RECOVERED = 60, _('Recovered'), ColorEnum.dark  # Device has been recovered
    CANCELLED = 90, _('Cancelled'), ColorEnum.danger  # Deployment was cancelled


class DeploymentStatusGroups:
    """Groups for DeploymentStatus codes."""

    # Deployments which are on their way into the water
    PIPELINE = [
        DeploymentStatus.PLANNED.value,
        DeploymentStatus.IN_PRODUCTION.value,
        DeploymentStatus.READY.value,
        DeploymentStatus.SCHEDULED.value,
    ]

    # Deployments which are currently in the water
    ACTIVE = [DeploymentStatus.DEPLOYED.value]

    # Deployments which are finished
    CLOSED = [DeploymentStatus.RECOVERED.value, DeploymentStatus.CANCELLED.value]

    # Deployments whose device can be deployed (or scheduled on a trip)
    DEPLOYABLE = [DeploymentStatus.READY.value, DeploymentStatus.SCHEDULED.value]


class TaskStatus(StatusCode):
    """Defines a set of status codes for a MaintenanceTask."""

    PROPOSED = 10, _('Proposed'), ColorEnum.secondary  # Proposed by the planner
    SCHEDULED = 20, _('Scheduled'), ColorEnum.info  # Scheduled (e.g. on a trip)
    IN_PROGRESS = 30, _('In Progress'), ColorEnum.primary  # Work has started
    COMPLETED = 40, _('Completed'), ColorEnum.success  # Work is finished
    CANCELLED = 90, _('Cancelled'), ColorEnum.danger  # Task was cancelled


class TaskStatusGroups:
    """Groups for TaskStatus codes."""

    OPEN = [
        TaskStatus.PROPOSED.value,
        TaskStatus.SCHEDULED.value,
        TaskStatus.IN_PROGRESS.value,
    ]

    # Tasks which can be started
    STARTABLE = [TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value]


class TripStatus(StatusCode):
    """Defines a set of status codes for a FieldTrip."""

    PLANNING = 10, _('Planning'), ColorEnum.secondary  # Tasks are being planned
    KIT_READY = 20, _('Kit Ready'), ColorEnum.info  # Parts kit has been prepared
    IN_PROGRESS = 30, _('In Progress'), ColorEnum.primary  # Team is in the field
    RECONCILING = 40, _('Reconciling'), ColorEnum.warning  # Kit is being returned
    CLOSED = 50, _('Closed'), ColorEnum.success  # Trip is finished
    CANCELLED = 90, _('Cancelled'), ColorEnum.danger  # Trip was cancelled


class TripStatusGroups:
    """Groups for TripStatus codes."""

    OPEN = [
        TripStatus.PLANNING.value,
        TripStatus.KIT_READY.value,
        TripStatus.IN_PROGRESS.value,
        TripStatus.RECONCILING.value,
    ]

    # Trips whose kit can be prepared (stock moved into the kit)
    KIT = [
        TripStatus.PLANNING.value,
        TripStatus.KIT_READY.value,
        TripStatus.IN_PROGRESS.value,
    ]

    # Trips which can be started
    STARTABLE = [TripStatus.PLANNING.value, TripStatus.KIT_READY.value]

    # Trips which can be reconciled
    RECONCILABLE = [
        TripStatus.KIT_READY.value,
        TripStatus.IN_PROGRESS.value,
        TripStatus.RECONCILING.value,
    ]

    # Trips which can be cancelled (not started yet)
    CANCELLABLE = [TripStatus.PLANNING.value, TripStatus.KIT_READY.value]


class AlertStatus(StatusCode):
    """Defines a set of status codes for an Alert."""

    OPEN = 10, _('Open'), ColorEnum.danger  # Condition is active
    ACKNOWLEDGED = 20, _('Acknowledged'), ColorEnum.warning  # Someone is on it
    RESOLVED = 30, _('Resolved'), ColorEnum.success  # Condition has cleared


class AlertStatusGroups:
    """Groups for AlertStatus codes."""

    OPEN = [AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value]


class AlertSeverity(StatusCode):
    """Defines the severity of an Alert (higher is more severe)."""

    INFO = 10, _('Info'), ColorEnum.info
    WARNING = 20, _('Warning'), ColorEnum.warning
    CRITICAL = 30, _('Critical'), ColorEnum.danger


class HealthStatus(StatusCode):
    """Defines the overall health of a deployed device (cached by monitoring).

    Higher is worse, so ordering by health puts the worst devices last.
    """

    UNKNOWN = 10, _('Unknown'), ColorEnum.secondary  # No data yet
    OK = 20, _('OK'), ColorEnum.success  # All streams report on time
    DEGRADED = 30, _('Degraded'), ColorEnum.warning  # Something is late
    CRITICAL = 40, _('Critical'), ColorEnum.danger  # Essential data is missing


class DataStreamStatus(StatusCode):
    """Defines the state of a data stream (cached by monitoring)."""

    UNKNOWN = 10, _('Unknown'), ColorEnum.secondary  # No data yet
    OK = 20, _('OK'), ColorEnum.success  # Data arrives on time
    LATE = 30, _('Late'), ColorEnum.warning  # Data is late
    MISSING = 40, _('Missing'), ColorEnum.danger  # Data has stopped
