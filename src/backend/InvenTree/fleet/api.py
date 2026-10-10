"""JSON API for the Fleet app."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.urls import include, path
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import fleet.serializers as fleet_serializers
from data_exporter.mixins import DataExportViewMixin
from fleet.filters import (
    AlertFilter,
    DeploymentFilter,
    FieldTripFilter,
    FleetDeviceTypeFilter,
    MaintenanceTaskFilter,
    SiteFilter,
)
from fleet.models import (
    Alert,
    ChecklistResult,
    ChecklistTemplateItem,
    DataStream,
    Deployment,
    DeviceLink,
    DeviceState,
    FaultCode,
    FieldTrip,
    FleetDeviceType,
    InternalState,
    KitTemplateLine,
    MaintenanceAction,
    MaintenanceTask,
    Site,
    StreamTemplate,
    TripKitLine,
)
from fleet.services import device_state, monitoring, trips
from fleet.status_codes import (
    AlertSeverity,
    AlertStatus,
    AlertStatusGroups,
    DataStreamStatus,
    DeploymentStatus,
    DeploymentStatusGroups,
    HealthStatus,
    TaskStatus,
    TaskStatusGroups,
    TripStatus,
    TripStatusGroups,
)
from generic.states.api import StatusView
from InvenTree.api import meta_path
from InvenTree.fields import InvenTreeOutputOption, OutputConfiguration
from InvenTree.filters import SEARCH_ORDER_FILTER
from InvenTree.helpers import current_date
from InvenTree.mixins import (
    CreateAPI,
    ListAPI,
    ListCreateAPI,
    OutputOptionsMixin,
    RetrieveAPI,
    RetrieveUpdateAPI,
    RetrieveUpdateDestroyAPI,
)
from InvenTree.permissions import InvenTreeTokenMatchesOASRequirements, RolePermission
from users.permissions import check_user_role


class FleetRoleMixin:
    """Check the fleet role (instead of per-model permissions) for custom endpoints."""

    permission_classes = [
        IsAuthenticated,
        RolePermission,
        InvenTreeTokenMatchesOASRequirements,
    ]
    role_required = 'fleet'


class FleetActionMixin(FleetRoleMixin):
    """Base class for an action (e.g. a state transition) on one fleet object.

    As the core action endpoints (e.g. BuildIssue): the object is passed to the
    serializer in its context, and serializer.save() performs the action. The
    response is the updated object (200), plus any extra data from save().

    Actions change existing objects, so they need the fleet 'change' role (see
    rolemap); extra_roles lists the other roles the action needs, e.g.
    ('stock', 'change') for an action which moves stock.
    """

    rolemap = {'POST': 'change'}

    # Other roles needed by the action, e.g. ('stock', 'change')
    extra_roles: list[tuple[str, str]] = []

    # The model of the object, its key in the serializer context, and the
    # serializer of the response
    model = None
    context_key = ''
    response_serializer_class = None

    @property
    def required_alternate_scopes(self) -> dict:
        """The token scopes: the fleet role of the action and the extra roles."""
        scopes = [f'r:{self.rolemap["POST"]}:fleet']
        scopes += [f'r:{permission}:{role}' for role, permission in self.extra_roles]

        return {'OPTIONS': [['g:read']], 'POST': [scopes]}

    def get_target(self):
        """Return the object which the action applies to."""
        if getattr(self, '_target', None) is None:
            self._target = get_object_or_404(self.model, pk=self.kwargs.get('pk'))

        return self._target

    def get_serializer_context(self):
        """Pass the object to the serializer."""
        context = super().get_serializer_context()

        try:
            context[self.context_key] = self.get_target()
        except Exception:
            pass

        return context

    def post(self, request, *args, **kwargs):
        """Validate the input, perform the action, and return the object."""
        for role, permission in self.extra_roles:
            if not check_user_role(request.user, role, permission):
                raise PermissionDenied(
                    _('This action also requires the role') + f' {role}.{permission}'
                )

        target = self.get_target()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            extra = serializer.save()

        return self.get_response(target, extra)

    def get_response(self, target, extra) -> Response:
        """Return the updated object (and the extra data of the action)."""
        serializer_class = self.response_serializer_class

        instance = serializer_class.annotate_queryset(self.model.objects.all()).get(
            pk=target.pk
        )

        data = serializer_class(instance, context=self.get_serializer_context()).data

        if isinstance(extra, dict):
            data.update(extra)

        return Response(data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Device types
# ---------------------------------------------------------------------------


class FleetDeviceTypeMixin:
    """Mixin class for FleetDeviceType endpoints."""

    queryset = FleetDeviceType.objects.all()
    serializer_class = fleet_serializers.FleetDeviceTypeSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        queryset = super().get_queryset().select_related('part')
        return self.serializer_class.annotate_queryset(queryset)


class FleetDeviceTypeOutputOptions(OutputConfiguration):
    """Output options for the FleetDeviceType endpoints."""

    OPTIONS = [InvenTreeOutputOption('part_detail', default=True)]


class FleetDeviceTypeList(
    DataExportViewMixin, FleetDeviceTypeMixin, OutputOptionsMixin, ListCreateAPI
):
    """List and create fleet device types."""

    output_options = FleetDeviceTypeOutputOptions
    filterset_class = FleetDeviceTypeFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = ['part', 'pm_interval_days', 'active', 'deployment_count']
    ordering_field_aliases = {'part': ['part__name']}
    ordering = 'part'
    search_fields = ['part__name', 'part__IPN', 'part__description']


class FleetDeviceTypeDetail(
    FleetDeviceTypeMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI
):
    """Retrieve, update or delete a fleet device type."""

    output_options = FleetDeviceTypeOutputOptions

    def destroy(self, request, *args, **kwargs):
        """A device type with deployments cannot be deleted (deactivate it instead)."""
        if self.get_object().deployments.exists():
            raise ValidationError({
                'non_field_errors': [
                    _('This device type has deployments. Mark it inactive instead')
                ]
            })

        return super().destroy(request, *args, **kwargs)


class StreamTemplateMixin:
    """Mixin class for StreamTemplate endpoints."""

    queryset = StreamTemplate.objects.all()
    serializer_class = fleet_serializers.StreamTemplateSerializer


class StreamTemplateList(StreamTemplateMixin, ListCreateAPI):
    """List and create stream templates."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['device_type', 'essential']
    ordering_fields = ['key', 'name', 'essential', 'expected_interval_minutes']
    ordering = 'key'
    search_fields = ['key', 'name']


class StreamTemplateDetail(StreamTemplateMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a stream template."""


class ChecklistTemplateItemMixin:
    """Mixin class for ChecklistTemplateItem endpoints."""

    queryset = ChecklistTemplateItem.objects.all()
    serializer_class = fleet_serializers.ChecklistTemplateItemSerializer


class ChecklistTemplateItemList(ChecklistTemplateItemMixin, ListCreateAPI):
    """List and create the checklist template items of device types."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['device_type', 'task_type', 'kind', 'required']
    ordering_fields = ['sequence', 'text', 'kind', 'task_type', 'required']
    ordering = ['sequence', 'pk']
    search_fields = ['text']


class ChecklistTemplateItemDetail(ChecklistTemplateItemMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a checklist template item."""


class KitTemplateLineMixin:
    """Mixin class for KitTemplateLine endpoints."""

    queryset = KitTemplateLine.objects.all().select_related('part')
    serializer_class = fleet_serializers.KitTemplateLineSerializer


class KitTemplateLineList(KitTemplateLineMixin, ListCreateAPI):
    """List and create the kit template lines of device types."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['device_type', 'mode', 'part']
    ordering_fields = ['part', 'quantity', 'mode']
    ordering_field_aliases = {'part': ['part__name']}
    ordering = 'part'
    search_fields = ['part__name', 'part__IPN', 'part__description']


class KitTemplateLineDetail(KitTemplateLineMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a kit template line."""


class FaultCodeMixin:
    """Mixin class for FaultCode endpoints."""

    queryset = FaultCode.objects.all()
    serializer_class = fleet_serializers.FaultCodeSerializer


class FaultCodeList(FaultCodeMixin, ListCreateAPI):
    """List and create fault codes."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['category', 'active']
    ordering_fields = ['code', 'name', 'category']
    ordering = 'code'
    search_fields = ['code', 'name']


class FaultCodeDetail(FaultCodeMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a fault code (mark it inactive instead of deleting)."""


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------


class SiteMixin:
    """Mixin class for Site endpoints."""

    queryset = Site.objects.all()
    serializer_class = fleet_serializers.SiteSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        queryset = super().get_queryset()
        return self.serializer_class.annotate_queryset(queryset)


class SiteOutputOptions(OutputConfiguration):
    """Output options for the Site endpoints."""

    OPTIONS = [
        InvenTreeOutputOption(
            'client_detail',
            default=True,
            description='Include detailed information about the client',
        )
    ]


class SiteList(DataExportViewMixin, SiteMixin, OutputOptionsMixin, ListCreateAPI):
    """List and create sites."""

    output_options = SiteOutputOptions
    filterset_class = SiteFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = [
        'reference',
        'name',
        'coverage',
        'client',
        'project',
        'active',
        'deployment_count',
    ]
    ordering_field_aliases = {
        'reference': ['reference_int', 'reference'],
        'client': ['client__name'],
    }
    ordering = 'reference'
    search_fields = ['reference', 'name', 'project', 'client__name']


class SiteDetail(SiteMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a site."""

    output_options = SiteOutputOptions

    def destroy(self, request, *args, **kwargs):
        """A site with deployments cannot be deleted (deactivate it instead)."""
        if self.get_object().deployments.exists():
            raise ValidationError({
                'non_field_errors': [
                    _('This site has deployments. Mark it inactive instead')
                ]
            })

        return super().destroy(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Device links
# ---------------------------------------------------------------------------


class DeviceLinkMixin:
    """Mixin class for DeviceLink endpoints."""

    queryset = DeviceLink.objects.all().select_related('stock_item__part')
    serializer_class = fleet_serializers.DeviceLinkSerializer


class DeviceLinkList(DeviceLinkMixin, ListCreateAPI):
    """List and create device links."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['stock_item', 'state']
    ordering_fields = ['platform_id', 'stock_item']
    ordering_field_aliases = {'stock_item': ['stock_item__serial']}
    ordering = 'platform_id'
    search_fields = ['platform_id', 'stock_item__serial', 'firmware_version']


class DeviceLinkDetail(DeviceLinkMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a device link."""


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


class DeploymentMixin:
    """Mixin class for Deployment endpoints."""

    queryset = Deployment.objects.all()
    serializer_class = fleet_serializers.DeploymentSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        queryset = super().get_queryset()
        return self.serializer_class.annotate_queryset(queryset)


class DeploymentOutputOptions(OutputConfiguration):
    """Output options for the Deployment endpoints."""

    OPTIONS = [
        InvenTreeOutputOption(
            'device_type_detail',
            default=True,
            description='Include detailed information about the device type',
        ),
        InvenTreeOutputOption(
            'build_detail',
            default=True,
            description='Include detailed information about the build order',
        ),
        InvenTreeOutputOption(
            'device_detail',
            default=True,
            description='Include detailed information about the device',
        ),
        InvenTreeOutputOption(
            'site_detail',
            default=True,
            description='Include detailed information about the site',
        ),
        InvenTreeOutputOption(
            'client_detail',
            default=False,
            description='Include detailed information about the client',
        ),
        InvenTreeOutputOption(
            'risks',
            default=False,
            description='Include the readiness risks of pipeline deployments',
        ),
    ]


class DeploymentList(
    DataExportViewMixin, DeploymentMixin, OutputOptionsMixin, ListCreateAPI
):
    """List and plan deployments.

    - GET: List deployments (pipeline, active and closed)
    - POST: Plan a deployment by hand (status PLANNED)
    """

    output_options = DeploymentOutputOptions
    filterset_class = DeploymentFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = [
        'reference',
        'status',
        'site',
        'device',
        'device_type',
        'build',
        'target_date',
        'deployed_at',
        'recovered_at',
        'next_pm_date',
        'health',
        'last_contact',
        'coverage',
        'creation_date',
        'device_state',
    ]
    ordering_field_aliases = {
        'reference': ['reference_int', 'reference'],
        'site': ['site__name'],
        'device': ['device__serial_int', 'device__serial'],
        'device_type': ['device_type__part__name'],
        'build': ['build__reference_int'],
    }
    ordering = '-reference'
    search_fields = [
        'reference',
        'site__name',
        'site__reference',
        'device__serial',
        'build__reference',
        'device_type__part__name',
        'device__fleet_link__platform_id',
        'client__name',
    ]


class DeploymentDetail(DeploymentMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a deployment."""

    output_options = DeploymentOutputOptions

    def destroy(self, request, *args, **kwargs):
        """Only planned or cancelled deployments can be deleted."""
        deployment = self.get_object()

        if deployment.status not in [
            DeploymentStatus.PLANNED.value,
            DeploymentStatus.CANCELLED.value,
        ]:
            raise ValidationError({
                'non_field_errors': [
                    _('Only planned or cancelled deployments can be deleted')
                ]
            })

        return super().destroy(request, *args, **kwargs)


class DeploymentActionMixin(FleetActionMixin):
    """Base class for actions on a single deployment."""

    queryset = Deployment.objects.all()
    model = Deployment
    context_key = 'deployment'
    response_serializer_class = fleet_serializers.DeploymentSerializer

    @extend_schema(responses={200: fleet_serializers.DeploymentSerializer})
    def post(self, request, *args, **kwargs):
        """Perform the action, and return the deployment."""
        return super().post(request, *args, **kwargs)


class DeploymentCreateBuild(DeploymentActionMixin, CreateAPI):
    """Create the build order (quantity 1) for a planned deployment."""

    serializer_class = fleet_serializers.DeploymentCreateBuildSerializer
    rolemap = {'POST': 'add'}
    extra_roles = [('build', 'add')]


class DeploymentAssignDevice(DeploymentActionMixin, CreateAPI):
    """Assign an existing serialized unit to a pipeline deployment."""

    serializer_class = fleet_serializers.DeploymentAssignDeviceSerializer


class DeploymentDeploy(DeploymentActionMixin, CreateAPI):
    """Deploy the device of a ready deployment at its site."""

    serializer_class = fleet_serializers.DeploymentDeploySerializer
    extra_roles = [('stock', 'change')]


class DeploymentRecover(DeploymentActionMixin, CreateAPI):
    """Recover a deployed device into stock."""

    serializer_class = fleet_serializers.DeploymentRecoverSerializer
    extra_roles = [('stock', 'change')]


class DeploymentSetPosition(DeploymentActionMixin, CreateAPI):
    """Set the nominal position (and optionally the geofence radius) of a device."""

    serializer_class = fleet_serializers.DeploymentSetPositionSerializer


class DeploymentSetState(DeploymentActionMixin, CreateAPI):
    """Set or clear the internal state of the device (problem acknowledged, docked, decommissioned)."""

    serializer_class = fleet_serializers.DeploymentSetStateSerializer


class DeploymentVerify(DeploymentActionMixin, CreateAPI):
    """Check that a deployed device reports fresh data (nothing is stored)."""

    serializer_class = fleet_serializers.DeploymentVerifySerializer

    @extend_schema(responses={200: fleet_serializers.VerifyResultSerializer})
    def post(self, request, *args, **kwargs):
        """Fetch the live status of the device, and return the verify result."""
        return FleetActionMixin.post(self, request, *args, **kwargs)

    def get_response(self, target, extra) -> Response:
        """Return the verify result (nothing is stored)."""
        return Response(
            fleet_serializers.VerifyResultSerializer(extra).data,
            status=status.HTTP_200_OK,
        )


class DeploymentTrack(FleetRoleMixin, ListAPI):
    """Position track (downsampled fixes) of a deployment."""

    serializer_class = fleet_serializers.PositionFixSerializer
    required_alternate_scopes = {
        'GET': [['g:read'], ['r:view:fleet']],
        'OPTIONS': [['g:read']],
    }

    @extend_schema(
        parameters=[
            OpenApiParameter(
                'since', type=str, description='Only fixes after this time (ISO)'
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        """Return the position track."""
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        """Return the fixes of the deployment, oldest first."""
        deployment = get_object_or_404(Deployment, pk=self.kwargs.get('pk'))
        queryset = deployment.positions.order_by('timestamp')

        if since := self.request.query_params.get('since'):
            try:
                since = serializers.DateTimeField().to_internal_value(since)
            except serializers.ValidationError:
                raise ValidationError({'since': _('Invalid date')})

            queryset = queryset.filter(timestamp__gt=since)

        return queryset


# ---------------------------------------------------------------------------
# Data streams
# ---------------------------------------------------------------------------


class DataStreamMixin:
    """Mixin class for DataStream endpoints."""

    queryset = DataStream.objects.all()
    serializer_class = fleet_serializers.DataStreamSerializer


class DataStreamList(DataStreamMixin, ListCreateAPI):
    """List the data streams of deployments (filter with ?deployment=)."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['deployment', 'enabled', 'essential', 'state']
    ordering_fields = [
        'key',
        'name',
        'essential',
        'enabled',
        'state',
        'last_seen',
        'expected_interval_minutes',
    ]
    ordering = 'key'
    search_fields = ['key', 'name']


class DataStreamDetail(DataStreamMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a data stream (e.g. disable it, mark it essential)."""


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class AlertMixin:
    """Mixin class for Alert endpoints."""

    queryset = Alert.objects.all()
    serializer_class = fleet_serializers.AlertSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        return self.serializer_class.annotate_queryset(super().get_queryset())


class AlertOutputOptions(OutputConfiguration):
    """Output options for the Alert endpoints."""

    OPTIONS = [
        InvenTreeOutputOption(
            'deployment_detail',
            default=True,
            description='Include detailed information about the deployment',
        ),
        InvenTreeOutputOption(
            'site_detail',
            default=True,
            description='Include detailed information about the site',
        ),
    ]


class AlertList(DataExportViewMixin, AlertMixin, OutputOptionsMixin, ListAPI):
    """List alerts (alerts are raised by the system, not created by hand)."""

    output_options = AlertOutputOptions
    filterset_class = AlertFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = [
        'reference',
        'status',
        'severity',
        'alert_type',
        'opened_at',
        'resolved_at',
        'deployment',
        'site',
    ]
    ordering_field_aliases = {
        'reference': ['reference_int', 'reference'],
        'deployment': ['deployment__reference_int'],
        'site': ['site__name'],
    }
    ordering = '-opened_at'
    search_fields = [
        'reference',
        'message',
        'site__name',
        'deployment__reference',
        'deployment__device__serial',
        'stream__key',
    ]


class AlertDetail(AlertMixin, OutputOptionsMixin, RetrieveAPI):
    """Retrieve an alert."""

    output_options = AlertOutputOptions


class AlertActionMixin(FleetActionMixin):
    """Base class for actions on a single alert."""

    queryset = Alert.objects.all()
    model = Alert
    context_key = 'alert'
    response_serializer_class = fleet_serializers.AlertSerializer

    @extend_schema(responses={200: fleet_serializers.AlertSerializer})
    def post(self, request, *args, **kwargs):
        """Perform the action, and return the alert."""
        return super().post(request, *args, **kwargs)


class AlertAcknowledge(AlertActionMixin, CreateAPI):
    """Acknowledge an open alert."""

    serializer_class = fleet_serializers.AlertAcknowledgeSerializer


class AlertResolve(AlertActionMixin, CreateAPI):
    """Resolve an alert by hand.

    A monitoring alert whose condition persists is opened again on the next poll.
    """

    serializer_class = fleet_serializers.AlertResolveSerializer


class AlertCreateTask(AlertActionMixin, CreateAPI):
    """Create a corrective maintenance task for an alert (and acknowledge it)."""

    serializer_class = fleet_serializers.AlertCreateTaskSerializer

    @extend_schema(responses={201: fleet_serializers.MaintenanceTaskSerializer})
    def post(self, request, *args, **kwargs):
        """Create the task, and return it."""
        return FleetActionMixin.post(self, request, *args, **kwargs)

    def get_response(self, target, extra) -> Response:
        """Return the new task."""
        task = fleet_serializers.MaintenanceTaskSerializer.annotate_queryset(
            MaintenanceTask.objects.all()
        ).get(pk=extra.pk)

        return Response(
            fleet_serializers.MaintenanceTaskSerializer(
                task, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Maintenance tasks
# ---------------------------------------------------------------------------


class MaintenanceTaskMixin:
    """Mixin class for MaintenanceTask endpoints."""

    queryset = MaintenanceTask.objects.all()
    serializer_class = fleet_serializers.MaintenanceTaskSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        queryset = super().get_queryset()
        return self.serializer_class.annotate_queryset(queryset)


class MaintenanceTaskOutputOptions(OutputConfiguration):
    """Output options for the MaintenanceTask endpoints."""

    OPTIONS = [
        InvenTreeOutputOption(
            'device_detail',
            default=True,
            description='Include detailed information about the device',
        ),
        InvenTreeOutputOption(
            'deployment_detail',
            default=True,
            description='Include detailed information about the deployment',
        ),
        InvenTreeOutputOption(
            'site_detail',
            default=True,
            description='Include detailed information about the site',
        ),
    ]


class MaintenanceTaskList(
    DataExportViewMixin, MaintenanceTaskMixin, OutputOptionsMixin, ListCreateAPI
):
    """List and create maintenance tasks.

    - GET: List tasks (filter with ?open=, ?assigned_to_me=, ?deployment= ...)
    - POST: Create a task by hand (PROPOSED, or SCHEDULED with a date)
    """

    output_options = MaintenanceTaskOutputOptions
    filterset_class = MaintenanceTaskFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = [
        'reference',
        'status',
        'task_type',
        'device',
        'site',
        'deployment',
        'due_date',
        'scheduled_date',
        'started_at',
        'completed_at',
    ]
    ordering_field_aliases = {
        'reference': ['reference_int', 'reference'],
        'device': ['device__serial_int', 'device__serial'],
        'site': ['site__name'],
        'deployment': ['deployment__reference_int'],
    }
    ordering = '-reference'
    search_fields = [
        'reference',
        'description',
        'summary',
        'site__name',
        'deployment__reference',
        'device__serial',
        'device__part__name',
    ]


class MaintenanceTaskDetail(
    MaintenanceTaskMixin, OutputOptionsMixin, RetrieveUpdateDestroyAPI
):
    """Retrieve, update or delete a maintenance task."""

    output_options = MaintenanceTaskOutputOptions

    def destroy(self, request, *args, **kwargs):
        """Only proposed or cancelled tasks without actions can be deleted."""
        task = self.get_object()

        if (
            task.status not in [TaskStatus.PROPOSED.value, TaskStatus.CANCELLED.value]
            or task.actions.exists()
        ):
            raise ValidationError({
                'non_field_errors': [
                    _('Only proposed or cancelled tasks without actions can be deleted')
                ]
            })

        return super().destroy(request, *args, **kwargs)


class TaskActionMixin(FleetActionMixin):
    """Base class for actions on a single task."""

    queryset = MaintenanceTask.objects.all()
    model = MaintenanceTask
    context_key = 'task'
    response_serializer_class = fleet_serializers.MaintenanceTaskSerializer

    @extend_schema(responses={200: fleet_serializers.MaintenanceTaskSerializer})
    def post(self, request, *args, **kwargs):
        """Perform the action, and return the task."""
        return super().post(request, *args, **kwargs)


class TaskStart(TaskActionMixin, CreateAPI):
    """Start a task, and create its checklist."""

    serializer_class = fleet_serializers.TaskStartSerializer


class TaskComponentAction(TaskActionMixin, CreateAPI):
    """Add, remove, destroy or replace a component of the task's device."""

    serializer_class = fleet_serializers.TaskComponentActionSerializer
    extra_roles = [('stock', 'change')]


class TaskConsume(TaskActionMixin, CreateAPI):
    """Use a bulk consumable during a task."""

    serializer_class = fleet_serializers.TaskConsumeSerializer
    extra_roles = [('stock', 'change')]


class TaskReposition(TaskActionMixin, CreateAPI):
    """Move the nominal position of the deployed device."""

    serializer_class = fleet_serializers.TaskRepositionSerializer


class TaskRecordAction(TaskActionMixin, CreateAPI):
    """Record a repair, cleaning, firmware update or other action."""

    serializer_class = fleet_serializers.TaskRecordActionSerializer


class TaskVerify(TaskActionMixin, CreateAPI):
    """Check that the device reports fresh data (stored on the task)."""

    serializer_class = fleet_serializers.TaskVerifySerializer


class TaskComplete(TaskActionMixin, CreateAPI):
    """Close a task."""

    serializer_class = fleet_serializers.TaskCompleteSerializer


class TaskCancel(TaskActionMixin, CreateAPI):
    """Cancel a task."""

    serializer_class = fleet_serializers.TaskCancelSerializer


class TaskDeploy(TaskActionMixin, CreateAPI):
    """Deploy the device of a deployment (or swap) task."""

    serializer_class = fleet_serializers.TaskDeploySerializer
    extra_roles = [('stock', 'change')]


class ChecklistResultMixin:
    """Mixin class for ChecklistResult endpoints."""

    queryset = ChecklistResult.objects.all().select_related(
        'task', 'template_item', 'fault_code'
    )
    serializer_class = fleet_serializers.ChecklistResultSerializer


class ChecklistResultList(ChecklistResultMixin, ListAPI):
    """List the checklist of tasks (filter with ?task=)."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['task', 'result', 'required', 'fault_code']
    ordering_fields = ['sequence', 'text', 'result']
    ordering = ['sequence', 'pk']
    search_fields = ['text', 'note', 'value']


class ChecklistResultDetail(ChecklistResultMixin, RetrieveUpdateAPI):
    """Retrieve or fill in a checklist item (while the task is in progress)."""


class MaintenanceActionList(ListAPI):
    """List the actions of tasks (filter with ?task=)."""

    queryset = MaintenanceAction.objects.all().select_related(
        'component_in__part',
        'component_out__part',
        'part',
        'destination',
        'fault_code',
        'created_by',
    )
    serializer_class = fleet_serializers.MaintenanceActionSerializer
    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['task', 'action', 'fault_code', 'part']
    ordering_fields = ['created_at', 'action']
    ordering = ['created_at', 'pk']
    search_fields = [
        'note',
        'part__name',
        'component_in__serial',
        'component_out__serial',
    ]


# ---------------------------------------------------------------------------
# Calendar and overview
# ---------------------------------------------------------------------------


class FleetReadView(FleetRoleMixin, APIView):
    """Base class for read-only fleet views which are not tied to one model."""

    required_alternate_scopes = {
        'GET': [['g:read'], ['r:view:fleet']],
        'OPTIONS': [['g:read']],
    }


# ---------------------------------------------------------------------------
# Field trips
# ---------------------------------------------------------------------------


class FieldTripMixin:
    """Mixin class for FieldTrip endpoints."""

    queryset = FieldTrip.objects.all()
    serializer_class = fleet_serializers.FieldTripSerializer

    def get_queryset(self):
        """Annotate the queryset."""
        queryset = super().get_queryset()
        return self.serializer_class.annotate_queryset(queryset)


class FieldTripList(DataExportViewMixin, FieldTripMixin, ListCreateAPI):
    """List and create field trips.

    - GET: List trips (filter with ?open=, ?mine=, ?status= ...)
    - POST: Create a trip (with its kit location); tasks and ready
      deployments can be added at once
    """

    filterset_class = FieldTripFilter
    filter_backends = SEARCH_ORDER_FILTER
    ordering_fields = ['reference', 'status', 'title', 'start_date', 'end_date']
    ordering_field_aliases = {'reference': ['reference_int', 'reference']}
    ordering = '-start_date'
    search_fields = ['reference', 'title', 'vessel']


class FieldTripDetail(FieldTripMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a field trip."""

    def destroy(self, request, *args, **kwargs):
        """Only a planned or cancelled trip without tasks or kit stock can be deleted."""
        trip = self.get_object()

        if (
            trip.status not in [TripStatus.PLANNING.value, TripStatus.CANCELLED.value]
            or trip.tasks.filter(status__in=TaskStatusGroups.OPEN).exists()
        ):
            raise ValidationError({
                'non_field_errors': [
                    _(
                        'Only a planned or cancelled trip without open tasks can be deleted'
                    )
                ]
            })

        with transaction.atomic():
            trips.delete_kit_locations(trip)
            return super().destroy(request, *args, **kwargs)


class TripKitLineMixin:
    """Mixin class for TripKitLine endpoints."""

    queryset = TripKitLine.objects.all().select_related(
        'part', 'stock_item', 'task', 'trip'
    )
    serializer_class = fleet_serializers.TripKitLineSerializer

    def perform_destroy(self, instance):
        """Kit lines of a closed trip cannot be deleted."""
        if instance.trip.status not in TripStatusGroups.OPEN:
            raise ValidationError({
                'non_field_errors': [_('The kit of a closed trip cannot be changed')]
            })

        super().perform_destroy(instance)


class TripKitLineList(TripKitLineMixin, ListCreateAPI):
    """List the kit lines of trips (filter with ?trip=), or add one by hand."""

    filter_backends = SEARCH_ORDER_FILTER
    filterset_fields = ['trip', 'part', 'source', 'task', 'stock_item']
    ordering_fields = ['part', 'source', 'quantity_planned']
    ordering_field_aliases = {'part': ['part__name']}
    ordering = ['source', 'part__name', 'pk']
    search_fields = ['part__name', 'note', 'stock_item__serial']


class TripKitLineDetail(TripKitLineMixin, RetrieveUpdateDestroyAPI):
    """Retrieve, update or delete a trip kit line."""


def trip_kit_data(trip: FieldTrip) -> dict:
    """The kit of a trip (lines with availability, contents, removed)."""
    removed = trips.get_removed_location(trip)

    return fleet_serializers.TripKitSerializer({
        'trip': trip.pk,
        'kit_location': trip.kit_location_id,
        'removed_location': removed.pk if removed else None,
        'lines': trips.kit_lines(trip),
        'contents': trips
        .kit_stock(trip)
        .select_related('part', 'location')
        .order_by('part__name', 'serial', 'pk'),
        'removed': trips
        .removed_stock(trip)
        .select_related('part', 'location')
        .order_by('part__name', 'serial', 'pk'),
    }).data


class TripKit(FleetReadView):
    """The kit of a trip: lines with availability, kit contents, removed items."""

    @extend_schema(responses={200: fleet_serializers.TripKitSerializer})
    def get(self, request, *args, **kwargs):
        """Return the kit."""
        trip = get_object_or_404(FieldTrip, pk=self.kwargs.get('pk'))

        return Response(trip_kit_data(trip))


class TripActionMixin(FleetActionMixin):
    """Base class for actions on a single trip."""

    queryset = FieldTrip.objects.all()
    model = FieldTrip
    context_key = 'trip'
    response_serializer_class = fleet_serializers.FieldTripSerializer

    @extend_schema(responses={200: fleet_serializers.FieldTripSerializer})
    def post(self, request, *args, **kwargs):
        """Perform the action, and return the trip."""
        return super().post(request, *args, **kwargs)


class TripAddTasks(TripActionMixin, CreateAPI):
    """Add tasks, and ready deployments (as deployment tasks), to a trip."""

    serializer_class = fleet_serializers.TripAddTasksSerializer


class TripRemoveTask(TripActionMixin, CreateAPI):
    """Take a task which has not started off a trip."""

    serializer_class = fleet_serializers.TripRemoveTaskSerializer


class TripSuggestKit(TripActionMixin, CreateAPI):
    """Regenerate the suggested kit lines (lines added by hand are kept)."""

    serializer_class = fleet_serializers.TripSuggestKitSerializer


class TripPrepareKit(TripActionMixin, CreateAPI):
    """Move stock into the trip kit location."""

    serializer_class = fleet_serializers.TripPrepareKitSerializer
    extra_roles = [('stock', 'change')]


class TripStart(TripActionMixin, CreateAPI):
    """Start a trip."""

    serializer_class = fleet_serializers.TripStartSerializer


class TripReconcile(TripActionMixin, CreateAPI):
    """Return the kit to stock, and close the trip."""

    serializer_class = fleet_serializers.TripReconcileSerializer
    extra_roles = [('stock', 'change')]


class TripCancel(TripActionMixin, CreateAPI):
    """Cancel a trip which has not started."""

    serializer_class = fleet_serializers.TripCancelSerializer


class FleetCalendar(FleetReadView):
    """Calendar events: deployment target dates, task dates and trips."""

    @extend_schema(
        parameters=[
            OpenApiParameter('start', type=str, description='Start date (ISO)'),
            OpenApiParameter('end', type=str, description='End date (ISO)'),
        ],
        responses={200: fleet_serializers.CalendarEventSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        """Return the events between the start and end dates."""
        date_field = serializers.DateField(required=False, allow_null=True)

        try:
            start = date_field.to_internal_value(request.query_params['start'][:10])
        except (KeyError, serializers.ValidationError):
            start = current_date() - timedelta(days=31)

        try:
            end = date_field.to_internal_value(request.query_params['end'][:10])
        except (KeyError, serializers.ValidationError):
            end = start + timedelta(days=62)

        events = []

        for dep in Deployment.objects.filter(
            status__in=DeploymentStatusGroups.PIPELINE,
            target_date__gte=start,
            target_date__lte=end,
        ).select_related('site'):
            title = dep.reference

            if dep.site:
                title += f' - {dep.site.name}'

            events.append({
                'model_type': 'deployment',
                'pk': dep.pk,
                'title': title,
                'start': dep.target_date,
                'end': None,
                'status': dep.status,
                'status_text': dep.get_status_display(),
                'url': dep.get_absolute_url(),
            })

        for task in (
            MaintenanceTask.objects
            .filter(status__in=TaskStatusGroups.OPEN)
            .annotate(when=Coalesce('scheduled_date', 'due_date'))
            .filter(when__gte=start, when__lte=end)
            .select_related('site')
        ):
            when = task.when

            title = task.reference

            if task.site:
                title += f' - {task.site.name}'

            events.append({
                'model_type': 'maintenancetask',
                'pk': task.pk,
                'title': title,
                'start': when,
                'end': None,
                'status': task.status,
                'status_text': task.get_status_display(),
                'url': task.get_absolute_url(),
            })

        for trip in (
            FieldTrip.objects
            .filter(start_date__lte=end)
            .annotate(last_day=Coalesce('end_date', 'start_date'))
            .filter(last_day__gte=start)
            .exclude(status=TripStatus.CANCELLED.value)
        ):
            events.append({
                'model_type': 'fieldtrip',
                'pk': trip.pk,
                'title': f'{trip.reference} - {trip.title}',
                'start': trip.start_date,
                'end': trip.end_date,
                'status': trip.status,
                'status_text': trip.get_status_display(),
                'url': trip.get_absolute_url(),
            })

        events.sort(key=lambda event: (event['start'], event['title']))

        return Response(
            fleet_serializers.CalendarEventSerializer(events, many=True).data
        )


class FleetOverview(FleetReadView):
    """KPI counts for the fleet overview."""

    @extend_schema(responses={200: fleet_serializers.FleetOverviewSerializer})
    def get(self, request, *args, **kwargs):
        """Return the KPI counts."""
        today = current_date()
        deployments = Deployment.objects.all()
        pipeline_qs = deployments.filter(status__in=DeploymentStatusGroups.PIPELINE)
        deployed = deployments.filter(status__in=DeploymentStatusGroups.ACTIVE)

        # Docked (and decommissioned) devices are not counted as deployed
        active = deployed.exclude(device_state.hidden_q())

        in_30 = today + timedelta(days=30)

        def by_status(value):
            return Count('pk', filter=Q(status=value))

        data = deployments.aggregate(
            planned=by_status(DeploymentStatus.PLANNED.value),
            in_production=by_status(DeploymentStatus.IN_PRODUCTION.value),
            ready=by_status(DeploymentStatus.READY.value),
            scheduled=by_status(DeploymentStatus.SCHEDULED.value),
        )

        data.update(
            pipeline_qs.aggregate(
                to_deploy_30=Count('pk', filter=Q(target_date__lte=in_30)),
                unscheduled=Count('pk', filter=Q(target_date__isnull=True)),
            )
        )

        data.update(
            active.aggregate(
                deployed=Count('pk'),
                pm_due_30=Count(
                    'pk', filter=Q(next_pm_date__gte=today, next_pm_date__lte=in_30)
                ),
                pm_overdue=Count('pk', filter=Q(next_pm_date__lt=today)),
                no_position=Count(
                    'pk', filter=Q(latitude__isnull=True) | Q(longitude__isnull=True)
                ),
                no_site=Count('pk', filter=Q(site__isnull=True)),
                **{
                    f'health_{member.name.lower()}': Count(
                        'pk', filter=Q(health=member.value)
                    )
                    for member in HealthStatus
                },
            )
        )

        data.update(
            Alert.objects.filter(status__in=AlertStatusGroups.OPEN).aggregate(
                open_alerts=Count('pk'),
                **{
                    f'alerts_{member.name.lower()}': Count(
                        'pk', filter=Q(severity=member.value)
                    )
                    for member in AlertSeverity
                },
            )
        )

        data.update(
            MaintenanceTask.objects.filter(status__in=TaskStatusGroups.OPEN).aggregate(
                tasks_open=Count('pk'),
                tasks_in_progress=Count(
                    'pk', filter=Q(status=TaskStatus.IN_PROGRESS.value)
                ),
                tasks_overdue=Count('pk', filter=Q(due_date__lt=today)),
            )
        )

        states = dict(
            device_state
            .annotate_device_state(deployed, today=today)
            .values_list('device_state')
            .annotate(count=Count('pk'))
            .order_by()
        )

        for value in DeviceState.values:
            data[f'state_{value.lower()}'] = states.get(value, 0)

        data['state_decommissioned'] = DeviceLink.objects.filter(
            state=InternalState.DECOMMISSIONED
        ).count()

        return Response(fleet_serializers.FleetOverviewSerializer(data).data)


class DeploymentMap(FleetReadView):
    """Compact list of deployed devices for the fleet map."""

    @extend_schema(
        parameters=[
            OpenApiParameter('site', type=int, description='Only this site'),
            OpenApiParameter(
                'deployment', type=int, description='Only this deployment'
            ),
            OpenApiParameter(
                'health', type=int, description='Only this health (HealthStatus)'
            ),
        ],
        responses={200: fleet_serializers.DeploymentMapSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        """Return the deployed devices with their position and geofence.

        Docked devices are on land, so they are left out.
        """
        queryset = device_state.annotate_device_state(
            Deployment.objects
            .filter(status__in=DeploymentStatusGroups.ACTIVE)
            .exclude(device_state.hidden_q())
            .select_related('site', 'device')
            .annotate(
                open_alert_count=Count(
                    'alerts',
                    filter=Q(alerts__status__in=AlertStatusGroups.OPEN),
                    distinct=True,
                )
            )
            .order_by('reference_int')
        )

        for param in ['site', 'deployment']:
            if value := request.query_params.get(param):
                try:
                    value = int(value)
                except ValueError:
                    raise ValidationError({param: _('Invalid value')})

                queryset = queryset.filter(**{
                    'site' if param == 'site' else 'pk': value
                })

        if health := request.query_params.get('health'):
            try:
                queryset = queryset.filter(health=int(health))
            except ValueError:
                raise ValidationError({'health': _('Invalid value')})

        def as_float(value):
            return float(value) if value is not None else None

        rows = []

        for dep in queryset:
            fence = monitoring.get_geofence(dep)
            nominal_lat = as_float(fence['latitude'])
            nominal_lon = as_float(fence['longitude'])
            last_lat = as_float(dep.last_latitude)
            last_lon = as_float(dep.last_longitude)
            has_last = last_lat is not None and last_lon is not None

            rows.append({
                'pk': dep.pk,
                'reference': dep.reference,
                'status': dep.status,
                'site': dep.site.pk if dep.site else None,
                'site_name': dep.site.name if dep.site else None,
                'site_reference': dep.site.reference if dep.site else None,
                'device_serial': dep.device.serial if dep.device else None,
                'latitude': last_lat if has_last else nominal_lat,
                'longitude': last_lon if has_last else nominal_lon,
                'nominal_latitude': nominal_lat,
                'nominal_longitude': nominal_lon,
                'last_latitude': last_lat,
                'last_longitude': last_lon,
                'last_position_at': dep.last_position_at,
                'distance_from_nominal_m': as_float(dep.distance_from_nominal_m),
                'radius_m': fence['radius_m'],
                'polygon': fence['polygon'],
                'health': dep.health,
                'last_contact': dep.last_contact,
                'open_alert_count': dep.open_alert_count,
                'device_state': dep.device_state,
            })

        return Response(fleet_serializers.DeploymentMapSerializer(rows, many=True).data)


fleet_api_urls = [
    # Device types (and their stream templates)
    path(
        'device-type/',
        include([
            path(
                'stream-template/',
                include([
                    path(
                        '<int:pk>/',
                        StreamTemplateDetail.as_view(),
                        name='api-fleet-stream-template-detail',
                    ),
                    path(
                        '',
                        StreamTemplateList.as_view(),
                        name='api-fleet-stream-template-list',
                    ),
                ]),
            ),
            path(
                'checklist-item/',
                include([
                    path(
                        '<int:pk>/',
                        ChecklistTemplateItemDetail.as_view(),
                        name='api-fleet-checklist-item-detail',
                    ),
                    path(
                        '',
                        ChecklistTemplateItemList.as_view(),
                        name='api-fleet-checklist-item-list',
                    ),
                ]),
            ),
            path(
                'kit-line/',
                include([
                    path(
                        '<int:pk>/',
                        KitTemplateLineDetail.as_view(),
                        name='api-fleet-kit-line-detail',
                    ),
                    path(
                        '',
                        KitTemplateLineList.as_view(),
                        name='api-fleet-kit-line-list',
                    ),
                ]),
            ),
            path(
                '<int:pk>/',
                include([
                    meta_path(FleetDeviceType),
                    path(
                        '',
                        FleetDeviceTypeDetail.as_view(),
                        name='api-fleet-device-type-detail',
                    ),
                ]),
            ),
            path('', FleetDeviceTypeList.as_view(), name='api-fleet-device-type-list'),
        ]),
    ),
    # Sites
    path(
        'site/',
        include([
            path(
                '<int:pk>/',
                include([
                    meta_path(Site),
                    path('', SiteDetail.as_view(), name='api-fleet-site-detail'),
                ]),
            ),
            path('', SiteList.as_view(), name='api-fleet-site-list'),
        ]),
    ),
    # Device links
    path(
        'device-link/',
        include([
            path(
                '<int:pk>/',
                include([
                    meta_path(DeviceLink),
                    path(
                        '',
                        DeviceLinkDetail.as_view(),
                        name='api-fleet-device-link-detail',
                    ),
                ]),
            ),
            path('', DeviceLinkList.as_view(), name='api-fleet-device-link-list'),
        ]),
    ),
    # Deployments
    path(
        'deployment/',
        include([
            path(
                'stream/',
                include([
                    path(
                        '<int:pk>/',
                        DataStreamDetail.as_view(),
                        name='api-fleet-stream-detail',
                    ),
                    path('', DataStreamList.as_view(), name='api-fleet-stream-list'),
                ]),
            ),
            path('map/', DeploymentMap.as_view(), name='api-fleet-deployment-map'),
            path(
                '<int:pk>/',
                include([
                    path(
                        'verify/',
                        DeploymentVerify.as_view(),
                        name='api-fleet-deployment-verify',
                    ),
                    path(
                        'track/',
                        DeploymentTrack.as_view(),
                        name='api-fleet-deployment-track',
                    ),
                    path(
                        'create-build/',
                        DeploymentCreateBuild.as_view(),
                        name='api-fleet-deployment-create-build',
                    ),
                    path(
                        'assign-device/',
                        DeploymentAssignDevice.as_view(),
                        name='api-fleet-deployment-assign-device',
                    ),
                    path(
                        'deploy/',
                        DeploymentDeploy.as_view(),
                        name='api-fleet-deployment-deploy',
                    ),
                    path(
                        'recover/',
                        DeploymentRecover.as_view(),
                        name='api-fleet-deployment-recover',
                    ),
                    path(
                        'set-position/',
                        DeploymentSetPosition.as_view(),
                        name='api-fleet-deployment-set-position',
                    ),
                    path(
                        'set-state/',
                        DeploymentSetState.as_view(),
                        name='api-fleet-deployment-set-state',
                    ),
                    meta_path(Deployment),
                    path(
                        '',
                        DeploymentDetail.as_view(),
                        name='api-fleet-deployment-detail',
                    ),
                ]),
            ),
            path('', DeploymentList.as_view(), name='api-fleet-deployment-list'),
        ]),
    ),
    # Alerts
    path(
        'alert/',
        include([
            path(
                '<int:pk>/',
                include([
                    path(
                        'acknowledge/',
                        AlertAcknowledge.as_view(),
                        name='api-fleet-alert-acknowledge',
                    ),
                    path(
                        'resolve/',
                        AlertResolve.as_view(),
                        name='api-fleet-alert-resolve',
                    ),
                    path(
                        'create-task/',
                        AlertCreateTask.as_view(),
                        name='api-fleet-alert-create-task',
                    ),
                    meta_path(Alert),
                    path('', AlertDetail.as_view(), name='api-fleet-alert-detail'),
                ]),
            ),
            path('', AlertList.as_view(), name='api-fleet-alert-list'),
        ]),
    ),
    # Maintenance tasks (and their checklist and actions)
    path(
        'task/',
        include([
            path(
                'checklist/',
                include([
                    path(
                        '<int:pk>/',
                        ChecklistResultDetail.as_view(),
                        name='api-fleet-checklist-result-detail',
                    ),
                    path(
                        '',
                        ChecklistResultList.as_view(),
                        name='api-fleet-checklist-result-list',
                    ),
                ]),
            ),
            path(
                'action/',
                MaintenanceActionList.as_view(),
                name='api-fleet-task-action-list',
            ),
            path(
                '<int:pk>/',
                include([
                    path('start/', TaskStart.as_view(), name='api-fleet-task-start'),
                    path(
                        'component-action/',
                        TaskComponentAction.as_view(),
                        name='api-fleet-task-component-action',
                    ),
                    path(
                        'consume/', TaskConsume.as_view(), name='api-fleet-task-consume'
                    ),
                    path(
                        'reposition/',
                        TaskReposition.as_view(),
                        name='api-fleet-task-reposition',
                    ),
                    path(
                        'record-action/',
                        TaskRecordAction.as_view(),
                        name='api-fleet-task-record-action',
                    ),
                    path('verify/', TaskVerify.as_view(), name='api-fleet-task-verify'),
                    path(
                        'complete/',
                        TaskComplete.as_view(),
                        name='api-fleet-task-complete',
                    ),
                    path('cancel/', TaskCancel.as_view(), name='api-fleet-task-cancel'),
                    path('deploy/', TaskDeploy.as_view(), name='api-fleet-task-deploy'),
                    meta_path(MaintenanceTask),
                    path(
                        '',
                        MaintenanceTaskDetail.as_view(),
                        name='api-fleet-task-detail',
                    ),
                ]),
            ),
            path('', MaintenanceTaskList.as_view(), name='api-fleet-task-list'),
        ]),
    ),
    # Field trips (and their kit lines)
    path(
        'trip/',
        include([
            path(
                'kit-line/',
                include([
                    path(
                        '<int:pk>/',
                        TripKitLineDetail.as_view(),
                        name='api-fleet-trip-kit-line-detail',
                    ),
                    path(
                        '',
                        TripKitLineList.as_view(),
                        name='api-fleet-trip-kit-line-list',
                    ),
                ]),
            ),
            path(
                '<int:pk>/',
                include([
                    path(
                        'add-tasks/',
                        TripAddTasks.as_view(),
                        name='api-fleet-trip-add-tasks',
                    ),
                    path(
                        'remove-task/',
                        TripRemoveTask.as_view(),
                        name='api-fleet-trip-remove-task',
                    ),
                    path(
                        'suggest-kit/',
                        TripSuggestKit.as_view(),
                        name='api-fleet-trip-suggest-kit',
                    ),
                    path('kit/', TripKit.as_view(), name='api-fleet-trip-kit'),
                    path(
                        'prepare-kit/',
                        TripPrepareKit.as_view(),
                        name='api-fleet-trip-prepare-kit',
                    ),
                    path('start/', TripStart.as_view(), name='api-fleet-trip-start'),
                    path(
                        'reconcile/',
                        TripReconcile.as_view(),
                        name='api-fleet-trip-reconcile',
                    ),
                    path('cancel/', TripCancel.as_view(), name='api-fleet-trip-cancel'),
                    meta_path(FieldTrip),
                    path('', FieldTripDetail.as_view(), name='api-fleet-trip-detail'),
                ]),
            ),
            path('', FieldTripList.as_view(), name='api-fleet-trip-list'),
        ]),
    ),
    # Fault codes
    path(
        'fault-code/',
        include([
            path(
                '<int:pk>/',
                FaultCodeDetail.as_view(),
                name='api-fleet-fault-code-detail',
            ),
            path('', FaultCodeList.as_view(), name='api-fleet-fault-code-list'),
        ]),
    ),
    path('calendar/', FleetCalendar.as_view(), name='api-fleet-calendar'),
    path('overview/', FleetOverview.as_view(), name='api-fleet-overview'),
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
            path(
                'severity/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: AlertSeverity},
                name='api-fleet-alert-severity-codes',
            ),
            path(
                'health/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: HealthStatus},
                name='api-fleet-health-codes',
            ),
            path(
                'stream/',
                StatusView.as_view(),
                {StatusView.MODEL_REF: DataStreamStatus},
                name='api-fleet-stream-status-codes',
            ),
        ]),
    ),
]
