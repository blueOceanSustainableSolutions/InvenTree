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


class AlertStatus(StatusCode):
    """Defines a set of status codes for an Alert."""

    OPEN = 10, _('Open'), ColorEnum.danger  # Condition is active
    ACKNOWLEDGED = 20, _('Acknowledged'), ColorEnum.warning  # Someone is on it
    RESOLVED = 30, _('Resolved'), ColorEnum.success  # Condition has cleared


class AlertStatusGroups:
    """Groups for AlertStatus codes."""

    OPEN = [AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value]
