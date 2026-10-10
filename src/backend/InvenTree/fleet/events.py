"""Event definitions and triggers for the fleet app.

Events are triggered with plugin.events.trigger_event, as in the core apps.
They only reach anything when plugin events are enabled; the fleet app itself
reacts to changes with Django signals (see signals.py).
"""

from fleet.status_codes import AlertStatus, DeploymentStatus, TaskStatus, TripStatus
from generic.events import BaseEventEnum


class FleetEvents(BaseEventEnum):
    """Event enumeration for the fleet app."""

    # Deployment events
    DEPLOYMENT_IN_PRODUCTION = 'deployment.in_production'
    DEPLOYMENT_READY = 'deployment.ready'
    DEPLOYMENT_SCHEDULED = 'deployment.scheduled'
    DEPLOYMENT_DEPLOYED = 'deployment.deployed'
    DEPLOYMENT_RECOVERED = 'deployment.recovered'
    DEPLOYMENT_CANCELLED = 'deployment.cancelled'
    DEVICE_STATE_CHANGED = 'deployment.device_state_changed'

    # Alert events
    ALERT_OPENED = 'alert.opened'
    ALERT_ACKNOWLEDGED = 'alert.acknowledged'
    ALERT_RESOLVED = 'alert.resolved'

    # Maintenance task events
    TASK_SCHEDULED = 'maintenancetask.scheduled'
    TASK_STARTED = 'maintenancetask.started'
    TASK_COMPLETED = 'maintenancetask.completed'
    TASK_CANCELLED = 'maintenancetask.cancelled'

    # Field trip events
    TRIP_KIT_READY = 'fieldtrip.kit_ready'
    TRIP_STARTED = 'fieldtrip.started'
    TRIP_RECONCILING = 'fieldtrip.reconciling'
    TRIP_CLOSED = 'fieldtrip.closed'
    TRIP_CANCELLED = 'fieldtrip.cancelled'


# The event triggered when a fleet object reaches a status (by model name)
STATUS_EVENTS = {
    'deployment': {
        DeploymentStatus.IN_PRODUCTION.value: FleetEvents.DEPLOYMENT_IN_PRODUCTION,
        DeploymentStatus.READY.value: FleetEvents.DEPLOYMENT_READY,
        DeploymentStatus.SCHEDULED.value: FleetEvents.DEPLOYMENT_SCHEDULED,
        DeploymentStatus.DEPLOYED.value: FleetEvents.DEPLOYMENT_DEPLOYED,
        DeploymentStatus.RECOVERED.value: FleetEvents.DEPLOYMENT_RECOVERED,
        DeploymentStatus.CANCELLED.value: FleetEvents.DEPLOYMENT_CANCELLED,
    },
    'alert': {
        AlertStatus.OPEN.value: FleetEvents.ALERT_OPENED,
        AlertStatus.ACKNOWLEDGED.value: FleetEvents.ALERT_ACKNOWLEDGED,
        AlertStatus.RESOLVED.value: FleetEvents.ALERT_RESOLVED,
    },
    'maintenancetask': {
        TaskStatus.SCHEDULED.value: FleetEvents.TASK_SCHEDULED,
        TaskStatus.IN_PROGRESS.value: FleetEvents.TASK_STARTED,
        TaskStatus.COMPLETED.value: FleetEvents.TASK_COMPLETED,
        TaskStatus.CANCELLED.value: FleetEvents.TASK_CANCELLED,
    },
    'fieldtrip': {
        TripStatus.KIT_READY.value: FleetEvents.TRIP_KIT_READY,
        TripStatus.IN_PROGRESS.value: FleetEvents.TRIP_STARTED,
        TripStatus.RECONCILING.value: FleetEvents.TRIP_RECONCILING,
        TripStatus.CLOSED.value: FleetEvents.TRIP_CLOSED,
        TripStatus.CANCELLED.value: FleetEvents.TRIP_CANCELLED,
    },
}


def trigger_status_event(instance) -> None:
    """Trigger the event for the status which a fleet object has just reached.

    Called by the services right after a status change is saved, as the core
    apps call trigger_event() in their state transition actions.
    """
    trigger_status_events(instance._meta.model_name, [instance.pk], instance.status)


def trigger_status_events(model_name: str, pks, status: int) -> None:
    """Trigger the status event for objects whose status was changed in bulk."""
    from plugin.events import trigger_event

    if event := STATUS_EVENTS.get(model_name, {}).get(int(status)):
        for pk in pks:
            trigger_event(event, id=pk)
