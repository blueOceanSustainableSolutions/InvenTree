"""Filter sets for the fleet API."""

from django.db.models import Exists, OuterRef, Q
from django.utils.translation import gettext_lazy as _

import django_filters.rest_framework.filters as rest_filters
from django_filters.rest_framework.filterset import FilterSet

from fleet.models import (
    Alert,
    Deployment,
    DeviceState,
    FieldTrip,
    FleetDeviceType,
    MaintenanceTask,
    Site,
)
from fleet.services.device_state import decommissioned_q
from fleet.status_codes import (
    AlertStatusGroups,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatusGroups,
    TripStatusGroups,
)
from InvenTree.filters import InvenTreeDateFilter
from InvenTree.helpers import current_date, str2bool
from stock.models import StockItem
from users.models import Owner


def filter_by_status(queryset, value):
    """Filter by integer status code, accounting for custom status codes."""
    q1 = Q(status=value, status_custom_key__isnull=True)
    q2 = Q(status_custom_key=value)

    return queryset.filter(q1 | q2).distinct()


class FleetDeviceTypeFilter(FilterSet):
    """Filter set for the FleetDeviceType list endpoint."""

    class Meta:
        """Metaclass options."""

        model = FleetDeviceType
        fields = ['part', 'active']


class SiteFilter(FilterSet):
    """Filter set for the Site list endpoint."""

    class Meta:
        """Metaclass options."""

        model = Site
        fields = ['coverage', 'active', 'client']

    has_active_deployment = rest_filters.BooleanFilter(
        label=_('Has Active Deployment'), method='filter_has_active_deployment'
    )

    def filter_has_active_deployment(self, queryset, name, value):
        """Filter by whether the site has a DEPLOYED deployment."""
        active = Deployment.objects.filter(
            site=OuterRef('pk'), status=DeploymentStatus.DEPLOYED.value
        )

        if str2bool(value):
            return queryset.filter(Exists(active))
        return queryset.exclude(Exists(active))


class DeploymentFilter(FilterSet):
    """Filter set for the Deployment list endpoint."""

    class Meta:
        """Metaclass options."""

        model = Deployment
        fields = [
            'site',
            'health',
            'coverage',
            'client',
            'device',
            'build',
            'device_type',
            'deployment_type',
        ]

    status = rest_filters.NumberFilter(label=_('Status'), method='filter_status')

    def filter_status(self, queryset, name, value):
        """Filter by integer status code."""
        return filter_by_status(queryset, value)

    pipeline = rest_filters.BooleanFilter(label=_('Pipeline'), method='filter_pipeline')

    def filter_pipeline(self, queryset, name, value):
        """Deployments which are not yet deployed (planned to scheduled)."""
        if str2bool(value):
            return queryset.filter(status__in=DeploymentStatusGroups.PIPELINE)
        return queryset.exclude(status__in=DeploymentStatusGroups.PIPELINE)

    active = rest_filters.BooleanFilter(label=_('Active'), method='filter_active')

    def filter_active(self, queryset, name, value):
        """Deployments which are deployed now."""
        if str2bool(value):
            return queryset.filter(status__in=DeploymentStatusGroups.ACTIVE)
        return queryset.exclude(status__in=DeploymentStatusGroups.ACTIVE)

    closed = rest_filters.BooleanFilter(label=_('Closed'), method='filter_closed')

    def filter_closed(self, queryset, name, value):
        """Deployments which are recovered or cancelled."""
        if str2bool(value):
            return queryset.filter(status__in=DeploymentStatusGroups.CLOSED)
        return queryset.exclude(status__in=DeploymentStatusGroups.CLOSED)

    unscheduled = rest_filters.BooleanFilter(
        label=_('Unscheduled'), method='filter_unscheduled'
    )

    def filter_unscheduled(self, queryset, name, value):
        """Pipeline deployments without a target date."""
        unscheduled = Q(
            status__in=DeploymentStatusGroups.PIPELINE, target_date__isnull=True
        )

        if str2bool(value):
            return queryset.filter(unscheduled)
        return queryset.exclude(unscheduled)

    has_device = rest_filters.BooleanFilter(
        label=_('Has Device'), field_name='device', lookup_expr='isnull', exclude=True
    )

    has_site = rest_filters.BooleanFilter(
        label=_('Has Site'), field_name='site', lookup_expr='isnull', exclude=True
    )

    has_position = rest_filters.BooleanFilter(
        label=_('Has Position'), method='filter_has_position'
    )

    def filter_has_position(self, queryset, name, value):
        """Filter by whether the nominal position (latitude and longitude) is set."""
        position = Q(latitude__isnull=False, longitude__isnull=False)

        if str2bool(value):
            return queryset.filter(position)
        return queryset.exclude(position)

    pm_due_before = InvenTreeDateFilter(
        label=_('PM due before'), field_name='next_pm_date', lookup_expr='lte'
    )

    pm_overdue = rest_filters.BooleanFilter(
        label=_('PM Overdue'), method='filter_pm_overdue'
    )

    def filter_pm_overdue(self, queryset, name, value):
        """Deployed devices whose next PM date has passed."""
        overdue = Q(
            status__in=DeploymentStatusGroups.ACTIVE, next_pm_date__lt=current_date()
        )

        if str2bool(value):
            return queryset.filter(overdue)
        return queryset.exclude(overdue)

    target_before = InvenTreeDateFilter(
        label=_('Target date before'), field_name='target_date', lookup_expr='lte'
    )

    target_after = InvenTreeDateFilter(
        label=_('Target date after'), field_name='target_date', lookup_expr='gte'
    )

    has_open_alerts = rest_filters.BooleanFilter(
        label=_('Has Open Alerts'), method='filter_has_open_alerts'
    )

    def filter_has_open_alerts(self, queryset, name, value):
        """Filter by whether the deployment has unresolved alerts."""
        alerts = Alert.objects.filter(
            deployment=OuterRef('pk'), status__in=AlertStatusGroups.OPEN
        )

        if str2bool(value):
            return queryset.filter(Exists(alerts))
        return queryset.exclude(Exists(alerts))

    device_state = rest_filters.ChoiceFilter(
        label=_('Device State'),
        choices=DeviceState.choices,
        method='filter_device_state',
    )

    def filter_device_state(self, queryset, name, value):
        """Filter by the effective device state (annotated by the serializer)."""
        return queryset.filter(device_state=value)

    decommissioned = rest_filters.BooleanFilter(
        label=_('Decommissioned'), method='filter_decommissioned'
    )

    def filter_decommissioned(self, queryset, name, value):
        """Filter by whether the device is decommissioned."""
        if str2bool(value):
            return queryset.filter(decommissioned_q())
        return queryset.exclude(decommissioned_q())


class AlertFilter(FilterSet):
    """Filter set for the Alert list endpoint."""

    class Meta:
        """Metaclass options."""

        model = Alert
        fields = [
            'severity',
            'alert_type',
            'deployment',
            'site',
            'stream',
            'resolution',
            'task',
        ]

    status = rest_filters.NumberFilter(label=_('Status'), method='filter_status')

    def filter_status(self, queryset, name, value):
        """Filter by integer status code."""
        return filter_by_status(queryset, value)

    open = rest_filters.BooleanFilter(label=_('Open'), method='filter_open')

    def filter_open(self, queryset, name, value):
        """Unresolved (open or acknowledged) alerts."""
        if str2bool(value):
            return queryset.filter(status__in=AlertStatusGroups.OPEN)
        return queryset.exclude(status__in=AlertStatusGroups.OPEN)

    device = rest_filters.ModelChoiceFilter(
        queryset=StockItem.objects.all(),
        label=_('Device'),
        field_name='deployment__device',
    )

    opened_after = InvenTreeDateFilter(
        label=_('Opened After'), field_name='opened_at', lookup_expr='gte'
    )

    opened_before = InvenTreeDateFilter(
        label=_('Opened Before'), field_name='opened_at', lookup_expr='lt'
    )


class MaintenanceTaskFilter(FilterSet):
    """Filter set for the MaintenanceTask list endpoint."""

    class Meta:
        """Metaclass options."""

        model = MaintenanceTask
        fields = ['task_type', 'site', 'trip', 'device', 'deployment', 'follow_up_of']

    status = rest_filters.NumberFilter(label=_('Status'), method='filter_status')

    def filter_status(self, queryset, name, value):
        """Filter by integer status code."""
        return filter_by_status(queryset, value)

    open = rest_filters.BooleanFilter(label=_('Open'), method='filter_open')

    def filter_open(self, queryset, name, value):
        """Tasks which are proposed, scheduled or in progress."""
        if str2bool(value):
            return queryset.filter(status__in=TaskStatusGroups.OPEN)
        return queryset.exclude(status__in=TaskStatusGroups.OPEN)

    overdue = rest_filters.BooleanFilter(label=_('Overdue'), method='filter_overdue')

    def filter_overdue(self, queryset, name, value):
        """Open tasks whose due date has passed."""
        overdue = Q(status__in=TaskStatusGroups.OPEN, due_date__lt=current_date())

        if str2bool(value):
            return queryset.filter(overdue)
        return queryset.exclude(overdue)

    has_trip = rest_filters.BooleanFilter(
        label=_('Has Trip'), field_name='trip', lookup_expr='isnull', exclude=True
    )

    due_before = InvenTreeDateFilter(
        label=_('Due before'), field_name='due_date', lookup_expr='lte'
    )

    due_after = InvenTreeDateFilter(
        label=_('Due after'), field_name='due_date', lookup_expr='gte'
    )

    min_date = rest_filters.DateFilter(label=_('Min Date'), method='filter_min_date')

    def filter_min_date(self, queryset, name, value):
        """Tasks whose date (scheduled, else due) is on or after this date (calendar)."""
        return queryset.filter(
            Q(scheduled_date__gte=value)
            | Q(scheduled_date__isnull=True, due_date__gte=value)
        )

    max_date = rest_filters.DateFilter(label=_('Max Date'), method='filter_max_date')

    def filter_max_date(self, queryset, name, value):
        """Tasks whose date (scheduled, else due) is on or before this date (calendar)."""
        return queryset.filter(
            Q(scheduled_date__lte=value)
            | Q(scheduled_date__isnull=True, due_date__lte=value)
        )

    assigned_to_me = rest_filters.BooleanFilter(
        label=_('Assigned to me'), method='filter_assigned_to_me'
    )

    def filter_assigned_to_me(self, queryset, name, value):
        """Tasks of the current user: technician, or on the trip team."""
        user = getattr(self.request, 'user', None)

        if user is None or not user.is_authenticated:
            return queryset.none() if str2bool(value) else queryset

        owners = Owner.get_owners_matching_user(user)

        mine = (
            Q(technicians=user)
            | Q(started_by=user)
            | Q(trip__team=user)
            | Q(trip__responsible__in=owners)
        )

        ids = MaintenanceTask.objects.filter(mine).values('pk')

        if str2bool(value):
            return queryset.filter(pk__in=ids)
        return queryset.exclude(pk__in=ids)


class FieldTripFilter(FilterSet):
    """Filter set for the FieldTrip list endpoint."""

    class Meta:
        """Metaclass options."""

        model = FieldTrip
        fields = ['responsible', 'team']

    status = rest_filters.NumberFilter(label=_('Status'), method='filter_status')

    def filter_status(self, queryset, name, value):
        """Filter by integer status code."""
        return filter_by_status(queryset, value)

    open = rest_filters.BooleanFilter(label=_('Open'), method='filter_open')

    def filter_open(self, queryset, name, value):
        """Trips which are not closed or cancelled."""
        if str2bool(value):
            return queryset.filter(status__in=TripStatusGroups.OPEN)
        return queryset.exclude(status__in=TripStatusGroups.OPEN)

    start_after = InvenTreeDateFilter(
        label=_('Start after'), field_name='start_date', lookup_expr='gte'
    )

    start_before = InvenTreeDateFilter(
        label=_('Start before'), field_name='start_date', lookup_expr='lte'
    )

    min_date = rest_filters.DateFilter(label=_('Min Date'), method='filter_min_date')

    def filter_min_date(self, queryset, name, value):
        """Trips which end (else start) on or after this date."""
        return queryset.filter(
            Q(end_date__gte=value) | Q(end_date__isnull=True, start_date__gte=value)
        )

    max_date = rest_filters.DateFilter(label=_('Max Date'), method='filter_max_date')

    def filter_max_date(self, queryset, name, value):
        """Trips which start on or before this date."""
        return queryset.filter(start_date__lte=value)

    mine = rest_filters.BooleanFilter(label=_('My Trips'), method='filter_mine')

    def filter_mine(self, queryset, name, value):
        """Trips of the current user: on the team, or responsible."""
        user = getattr(self.request, 'user', None)

        if user is None or not user.is_authenticated:
            return queryset.none() if str2bool(value) else queryset

        owners = Owner.get_owners_matching_user(user)

        ids = FieldTrip.objects.filter(Q(team=user) | Q(responsible__in=owners)).values(
            'pk'
        )

        if str2bool(value):
            return queryset.filter(pk__in=ids)
        return queryset.exclude(pk__in=ids)
