"""JSON API for the Fleet app."""

from django.urls import include, path

from fleet.status_codes import AlertStatus, DeploymentStatus, TaskStatus, TripStatus
from generic.states.api import StatusView

fleet_api_urls = [
    # Status code information
    path(
        'status/',
        include([
            path(
                'deployment/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: DeploymentStatus},
                name='api-fleet-deployment-status-codes',
            ),
            path(
                'task/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: TaskStatus},
                name='api-fleet-task-status-codes',
            ),
            path(
                'trip/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: TripStatus},
                name='api-fleet-trip-status-codes',
            ),
            path(
                'alert/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: AlertStatus},
                name='api-fleet-alert-status-codes',
            ),
        ]),
    )
]
