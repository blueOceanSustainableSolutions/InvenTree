"""JSON serializers for the fleet app."""

from decimal import Decimal

from django.db.models import Count, OuterRef, Q, Subquery
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

import company.serializers
import part.serializers as part_serializers
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
    PositionFix,
    Site,
    StreamTemplate,
    TripKitLine,
)
from fleet.services.device_state import annotate_device_state
from fleet.services.pipeline import readiness_risks
from fleet.status_codes import (
    AlertStatusGroups,
    DeploymentStatus,
    DeploymentStatusGroups,
    TaskStatus,
    TaskStatusGroups,
    TripStatusGroups,
)
from generic.states.fields import InvenTreeCustomStatusSerializerMixin
from importer.registry import register_importer
from InvenTree.helpers import current_date
from InvenTree.mixins import DataImportExportSerializerMixin
from InvenTree.serializers import (
    CustomStatusSerializerMixin,
    FilterableSerializerMixin,
    InvenTreeDecimalField,
    InvenTreeModelSerializer,
    NotesFieldMixin,
    OptionalField,
)
from stock.components import ComponentActionSerializer
from stock.models import StockItem, StockLocation
from users.serializers import OwnerSerializer, UserSerializer


class FleetDeviceBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for a serialized device (StockItem)."""

    class Meta:
        """Metaclass options."""

        model = StockItem
        fields = [
            'pk',
            'part',
            'part_name',
            'serial',
            'status',
            'customer',
            'location',
            'is_building',
        ]
        read_only_fields = fields

    part_name = serializers.CharField(
        source='part.full_name', read_only=True, label=_('Part Name')
    )


@register_importer()
class FleetDeviceTypeSerializer(
    FilterableSerializerMixin,
    NotesFieldMixin,
    DataImportExportSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the FleetDeviceType model."""

    class Meta:
        """Metaclass options."""

        model = FleetDeviceType
        fields = [
            'pk',
            'part',
            'part_detail',
            'pm_interval_days',
            'verify_window_minutes',
            'active',
            'notes',
            'stream_count',
            'checklist_count',
            'kit_line_count',
            'deployment_count',
        ]

    part_detail = OptionalField(
        serializer_class=part_serializers.PartBriefSerializer,
        serializer_kwargs={'source': 'part', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['part'],
    )

    stream_count = serializers.IntegerField(read_only=True, label=_('Streams'))

    checklist_count = serializers.IntegerField(
        read_only=True, label=_('Checklist Items')
    )

    kit_line_count = serializers.IntegerField(read_only=True, label=_('Kit Lines'))

    deployment_count = serializers.IntegerField(
        read_only=True, label=_('Active Deployments')
    )

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the stream and active deployment counts."""
        return queryset.annotate(
            stream_count=Count('stream_templates', distinct=True),
            checklist_count=Count('checklist_items', distinct=True),
            kit_line_count=Count('kit_lines', distinct=True),
            deployment_count=Count(
                'deployments',
                filter=Q(deployments__status=DeploymentStatus.DEPLOYED.value),
                distinct=True,
            ),
        )

    def validate_part(self, part):
        """A fleet device must be a trackable assembly."""
        if not part.assembly or not part.trackable:
            raise serializers.ValidationError(
                _('A fleet device type must be a trackable assembly')
            )

        return part


class StreamTemplateSerializer(InvenTreeModelSerializer):
    """Serializer for the StreamTemplate model."""

    class Meta:
        """Metaclass options."""

        model = StreamTemplate
        fields = [
            'pk',
            'device_type',
            'key',
            'name',
            'essential',
            'expected_interval_minutes',
            'grace_minutes',
        ]


class SiteBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for the Site model."""

    class Meta:
        """Metaclass options."""

        model = Site
        fields = ['pk', 'reference', 'name', 'latitude', 'longitude', 'coverage']
        read_only_fields = fields


@register_importer()
class SiteSerializer(
    FilterableSerializerMixin,
    NotesFieldMixin,
    DataImportExportSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the Site model."""

    class Meta:
        """Metaclass options."""

        model = Site
        fields = [
            'pk',
            'reference',
            'name',
            'latitude',
            'longitude',
            'depth_m',
            'coverage',
            'client',
            'client_detail',
            'project',
            'pm_interval_override_days',
            'geofence_radius_m',
            'geofence_polygon',
            'active',
            'notes',
            'active_deployment',
            'active_deployment_reference',
            'deployment_count',
        ]

    reference = serializers.CharField(required=False, label=_('Reference'))

    client_detail = OptionalField(
        serializer_class=company.serializers.CompanyBriefSerializer,
        serializer_kwargs={'source': 'client', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['client'],
    )

    active_deployment = serializers.IntegerField(
        read_only=True, allow_null=True, label=_('Active Deployment')
    )

    active_deployment_reference = serializers.CharField(
        read_only=True, allow_null=True, label=_('Active Deployment')
    )

    deployment_count = serializers.IntegerField(read_only=True, label=_('Deployments'))

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the active deployment and the number of deployments."""
        active = Deployment.objects.filter(
            site=OuterRef('pk'), status=DeploymentStatus.DEPLOYED.value
        )

        return queryset.annotate(
            active_deployment=Subquery(active.values('pk')[:1]),
            active_deployment_reference=Subquery(active.values('reference')[:1]),
            deployment_count=Count('deployments', distinct=True),
        )

    def validate_reference(self, reference):
        """The reference must match the reference pattern setting."""
        Site.validate_reference_field(reference)
        return reference


class DeviceLinkSerializer(FilterableSerializerMixin, InvenTreeModelSerializer):
    """Serializer for the DeviceLink model."""

    class Meta:
        """Metaclass options."""

        model = DeviceLink
        fields = [
            'pk',
            'stock_item',
            'stock_item_detail',
            'platform_id',
            'firmware_version',
            'comment',
            'state',
            'state_text',
            'state_note',
            'state_changed_at',
            'state_changed_by',
            'state_changed_by_detail',
        ]
        read_only_fields = [
            'state',
            'state_note',
            'state_changed_at',
            'state_changed_by',
        ]

    state_text = serializers.CharField(
        source='get_state_display', read_only=True, label=_('State')
    )

    state_changed_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'state_changed_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['state_changed_by'],
    )

    stock_item_detail = FleetDeviceBriefSerializer(
        source='stock_item', read_only=True, many=False
    )

    def validate_platform_id(self, platform_id):
        """Platform ids are unique when set."""
        platform_id = (platform_id or '').strip()

        if platform_id:
            others = DeviceLink.objects.filter(platform_id=platform_id)

            if self.instance:
                others = others.exclude(pk=self.instance.pk)

            if others.exists():
                raise serializers.ValidationError(
                    _('This platform ID is already linked to another device')
                )

        return platform_id


class DeviceTypeBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for the FleetDeviceType model."""

    class Meta:
        """Metaclass options."""

        model = FleetDeviceType
        fields = ['pk', 'part', 'part_name', 'pm_interval_days', 'active']
        read_only_fields = fields

    part_name = serializers.CharField(
        source='part.full_name', read_only=True, label=_('Part Name')
    )


class BuildBriefSerializer(serializers.Serializer):
    """Brief representation of the build order of a deployment."""

    pk = serializers.IntegerField(read_only=True)
    reference = serializers.CharField(read_only=True)
    status = serializers.IntegerField(read_only=True)
    status_text = serializers.CharField(source='get_status_display', read_only=True)
    status_custom_key = serializers.IntegerField(read_only=True, allow_null=True)
    target_date = serializers.DateField(read_only=True, allow_null=True)
    quantity = InvenTreeDecimalField(read_only=True)
    completed = InvenTreeDecimalField(read_only=True)


class ReadinessRiskSerializer(serializers.Serializer):
    """A readiness risk of a pipeline deployment."""

    code = serializers.CharField(read_only=True)
    severity = serializers.IntegerField(read_only=True, help_text='AlertSeverity')
    message = serializers.CharField(read_only=True)


class DeploymentSerializer(
    CustomStatusSerializerMixin,
    FilterableSerializerMixin,
    NotesFieldMixin,
    DataImportExportSerializerMixin,
    InvenTreeCustomStatusSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the Deployment model."""

    class Meta:
        """Metaclass options."""

        model = Deployment
        fields = [
            'pk',
            'reference',
            'creation_date',
            'status',
            'status_text',
            'status_custom_key',
            'device_type',
            'device_type_detail',
            'build',
            'build_detail',
            'device',
            'device_detail',
            'platform_id',
            'site',
            'site_detail',
            'deployment_type',
            'replaces',
            'replaces_reference',
            'client',
            'client_detail',
            'coverage',
            'target_date',
            'deployed_at',
            'recovered_at',
            'latitude',
            'longitude',
            'depth_m',
            'geofence_radius_m',
            'geofence_polygon',
            'next_pm_date',
            'next_pm_manual',
            'health',
            'health_text',
            'last_contact',
            'last_latitude',
            'last_longitude',
            'last_position_at',
            'distance_from_nominal_m',
            'last_polled_at',
            'open_alert_count',
            'device_state',
            'internal_state',
            'internal_state_note',
            'internal_state_changed_at',
            'internal_state_changed_by_detail',
            'barcode_hash',
            'notes',
            'metadata_warning',
            'risks',
        ]
        read_only_fields = [
            'creation_date',
            'status',
            'status_text',
            'status_custom_key',
            'build',
            'device',
            'deployed_at',
            'recovered_at',
            'health',
            'last_contact',
            'last_latitude',
            'last_longitude',
            'last_position_at',
            'distance_from_nominal_m',
            'last_polled_at',
        ]

    reference = serializers.CharField(required=False, label=_('Reference'))

    device_type_detail = OptionalField(
        serializer_class=DeviceTypeBriefSerializer,
        serializer_kwargs={'source': 'device_type', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['device_type', 'device_type__part'],
    )

    build_detail = OptionalField(
        serializer_class=BuildBriefSerializer,
        serializer_kwargs={
            'source': 'build',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['build'],
    )

    device_detail = OptionalField(
        serializer_class=FleetDeviceBriefSerializer,
        serializer_kwargs={
            'source': 'device',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['device', 'device__part'],
    )

    site_detail = OptionalField(
        serializer_class=SiteBriefSerializer,
        serializer_kwargs={
            'source': 'site',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['site'],
    )

    client_detail = OptionalField(
        serializer_class=company.serializers.CompanyBriefSerializer,
        serializer_kwargs={
            'source': 'client',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=False,
        prefetch_fields=['client'],
    )

    risks = OptionalField(
        serializer_class=serializers.SerializerMethodField,
        serializer_kwargs={'read_only': True},
        default_include=False,
    )

    platform_id = serializers.CharField(
        source='device.fleet_link.platform_id',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Platform ID'),
    )

    replaces_reference = serializers.CharField(
        source='replaces.reference',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Replaces'),
    )

    open_alert_count = serializers.IntegerField(read_only=True, label=_('Open Alerts'))

    device_state = serializers.ChoiceField(
        choices=DeviceState.choices,
        read_only=True,
        allow_null=True,
        label=_('Device State'),
        help_text=_('Internal state of the device (None outside of deployed)'),
    )

    internal_state = serializers.CharField(
        source='device.fleet_link.state',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Manual State'),
    )

    internal_state_note = serializers.CharField(
        source='device.fleet_link.state_note',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('State Note'),
    )

    internal_state_changed_at = serializers.DateTimeField(
        source='device.fleet_link.state_changed_at',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('State Changed'),
    )

    internal_state_changed_by_detail = UserSerializer(
        source='device.fleet_link.state_changed_by',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('State Changed By'),
    )

    health_text = serializers.CharField(
        source='get_health_display', read_only=True, label=_('Health')
    )

    barcode_hash = serializers.CharField(read_only=True)

    metadata_warning = serializers.SerializerMethodField(label=_('Warning'))

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the number of open alerts and the device state."""
        queryset = queryset.select_related(
            'replaces', 'device__fleet_link', 'device__fleet_link__state_changed_by'
        )

        queryset = queryset.annotate(
            open_alert_count=Count(
                'alerts',
                filter=Q(alerts__status__in=AlertStatusGroups.OPEN),
                distinct=True,
            )
        )

        return annotate_device_state(queryset)

    def get_metadata_warning(self, deployment) -> str | None:
        """Return the warning stored in the metadata (e.g. build quantity != 1)."""
        return (deployment.metadata or {}).get('warning')

    @extend_schema_field(ReadinessRiskSerializer(many=True))
    def get_risks(self, deployment) -> list[dict]:
        """Return the readiness risks of the deployment."""
        return readiness_risks(deployment)

    def validate(self, data):
        """Validate the deployment data."""
        data = super().validate(data)

        site = data.get('site')

        if (
            site is not None
            and self.instance is not None
            and self.instance.status == DeploymentStatus.DEPLOYED.value
        ):
            active = (
                Deployment.objects
                .filter(site=site, status=DeploymentStatus.DEPLOYED.value)
                .exclude(pk=self.instance.pk)
                .first()
            )

            if active is not None:
                raise serializers.ValidationError({
                    'site': _('The site already has an active deployment')
                    + f' ({active.reference})'
                })

        return data

    def validate_reference(self, reference):
        """The reference must match the reference pattern setting."""
        Deployment.validate_reference_field(reference)
        return reference

    def create(self, validated_data):
        """Copy the coverage from the site unless it was given."""
        site = validated_data.get('site')

        # initial_data is pre-filled with model defaults, so check the raw request
        request = self.context.get('request')
        given = getattr(request, 'data', None) or {}

        if site is not None and 'coverage' not in given:
            validated_data['coverage'] = site.coverage

        validated_data['status'] = DeploymentStatus.PLANNED.value

        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Setting a site on a deployed device fills an empty position from it.

        A position which is already set is never overwritten. Pipeline
        deployments take the site position when they are deployed.
        """
        if instance.status == DeploymentStatus.DEPLOYED.value:
            self.copy_site_position(validated_data, instance)

        return super().update(instance, validated_data)

    @staticmethod
    def copy_site_position(validated_data: dict, instance) -> None:
        """Copy the site position into the deployment if it has none."""
        site = validated_data.get('site')

        if site is None or site.latitude is None or site.longitude is None:
            return

        def current(field):
            if field in validated_data:
                return validated_data[field]
            return getattr(instance, field, None)

        if current('latitude') is None and current('longitude') is None:
            validated_data['latitude'] = site.latitude
            validated_data['longitude'] = site.longitude

            if current('depth_m') is None:
                validated_data['depth_m'] = site.depth_m


class FleetActionSerializer(serializers.Serializer):
    """Base class for the input of an action on one fleet object.

    As the core action serializers (e.g. BuildIssueSerializer), save()
    performs the action on the object which the view passes in the context
    (under CONTEXT_KEY), and returns extra response data (or None).
    """

    CONTEXT_KEY = ''

    @property
    def target(self):
        """The object which the action applies to."""
        return self.context[self.CONTEXT_KEY]

    @property
    def user(self):
        """The user performing the action."""
        return self.context['request'].user


class DeploymentActionSerializer(FleetActionSerializer):
    """Base class for an action on a deployment."""

    CONTEXT_KEY = 'deployment'


class AlertActionSerializer(FleetActionSerializer):
    """Base class for an action on an alert."""

    CONTEXT_KEY = 'alert'


class TaskActionSerializer(FleetActionSerializer):
    """Base class for an action on a maintenance task."""

    CONTEXT_KEY = 'task'


class TripActionSerializer(FleetActionSerializer):
    """Base class for an action on a field trip."""

    CONTEXT_KEY = 'trip'


class DeploymentCreateBuildSerializer(DeploymentActionSerializer):
    """Create the build order for a planned deployment (no input)."""

    def save(self):
        """Create the build order."""
        from fleet.services import pipeline

        pipeline.create_build_for(self.target, user=self.user)


class DeploymentAssignDeviceSerializer(DeploymentActionSerializer):
    """Assign an existing serialized unit to a deployment."""

    stock_item = serializers.PrimaryKeyRelatedField(
        queryset=StockItem.objects.all(),
        label=_('Device'),
        help_text=_('Serialized unit in stock'),
    )

    def save(self):
        """Assign the device."""
        from fleet.services import pipeline

        pipeline.assign_existing_device(
            self.target, self.validated_data['stock_item'], user=self.user
        )


class DeployFieldsSerializer(serializers.Serializer):
    """The inputs of a deploy action (the site is optional)."""

    latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
        required=False,
        allow_null=True,
        label=_('Latitude'),
        help_text=_(
            'Actual position (defaults to the deployment position, then the site position; may stay empty)'
        ),
    )

    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        required=False,
        allow_null=True,
        label=_('Longitude'),
    )

    depth_m = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
        label=_('Depth'),
        help_text=_('Water depth (m)'),
    )

    deployed_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        label=_('Deployed At'),
        help_text=_('Defaults to now'),
    )


class DeploymentDeploySerializer(DeploymentActionSerializer, DeployFieldsSerializer):
    """Deploy the device of a ready deployment."""

    def save(self):
        """Deploy the device."""
        data = self.validated_data

        self.target.deploy(
            user=self.user,
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            depth_m=data.get('depth_m'),
            deployed_at=data.get('deployed_at'),
        )


class DeploymentSetPositionSerializer(DeploymentActionSerializer):
    """Set the nominal position of a device."""

    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=-90, max_value=90, label=_('Latitude')
    )

    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        label=_('Longitude'),
    )

    depth_m = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
        label=_('Depth'),
        help_text=_('Water depth (m)'),
    )

    geofence_radius_m = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        label=_('Geofence Radius'),
        help_text=_('Geofence radius (m). Leave blank to keep the current value'),
    )

    def save(self):
        """Set the position."""
        from fleet.services import deployment

        data = self.validated_data

        deployment.set_position(
            self.target,
            latitude=data['latitude'],
            longitude=data['longitude'],
            depth_m=data.get('depth_m'),
            geofence_radius_m=data.get('geofence_radius_m'),
        )


class DeploymentSetStateSerializer(DeploymentActionSerializer):
    """Set (or clear) the internal state of the device of a deployment."""

    state = serializers.ChoiceField(
        choices=InternalState.choices,
        allow_blank=True,
        label=_('State'),
        help_text=_('Leave blank to clear the state'),
    )

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=250, label=_('Note')
    )

    def save(self):
        """Set the state."""
        from fleet.services import device_state

        device_state.set_state(
            self.target,
            self.validated_data.get('state', ''),
            user=self.user,
            note=self.validated_data.get('note', ''),
        )


class DeploymentRecoverSerializer(DeploymentActionSerializer):
    """Recover a deployed device into stock."""

    location = serializers.PrimaryKeyRelatedField(
        queryset=StockLocation.objects.filter(structural=False),
        required=False,
        allow_null=True,
        label=_('Location'),
        help_text=_('Defaults to the fleet workshop location'),
    )

    notes = serializers.CharField(
        required=False, allow_blank=True, max_length=250, label=_('Notes')
    )

    def save(self):
        """Recover the device."""
        self.target.recover(
            user=self.user,
            location=self.validated_data.get('location'),
            notes=self.validated_data.get('notes', ''),
        )


class CalendarEventSerializer(serializers.Serializer):
    """An event for the fleet calendar."""

    model_type = serializers.CharField(read_only=True)
    pk = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    start = serializers.DateField(read_only=True)
    end = serializers.DateField(read_only=True, allow_null=True)
    status = serializers.IntegerField(read_only=True)
    status_text = serializers.CharField(read_only=True)
    url = serializers.CharField(read_only=True)


class FleetOverviewSerializer(serializers.Serializer):
    """KPI counts for the fleet overview."""

    deployed = serializers.IntegerField(read_only=True)
    planned = serializers.IntegerField(read_only=True)
    in_production = serializers.IntegerField(read_only=True)
    ready = serializers.IntegerField(read_only=True)
    scheduled = serializers.IntegerField(read_only=True)
    to_deploy_30 = serializers.IntegerField(read_only=True)
    unscheduled = serializers.IntegerField(read_only=True)
    pm_due_30 = serializers.IntegerField(read_only=True)
    pm_overdue = serializers.IntegerField(read_only=True)
    no_position = serializers.IntegerField(read_only=True)
    no_site = serializers.IntegerField(read_only=True)
    health_ok = serializers.IntegerField(read_only=True)
    health_degraded = serializers.IntegerField(read_only=True)
    health_critical = serializers.IntegerField(read_only=True)
    health_unknown = serializers.IntegerField(read_only=True)
    open_alerts = serializers.IntegerField(read_only=True)
    alerts_critical = serializers.IntegerField(read_only=True)
    alerts_warning = serializers.IntegerField(read_only=True)
    alerts_info = serializers.IntegerField(read_only=True)
    tasks_open = serializers.IntegerField(read_only=True)
    tasks_in_progress = serializers.IntegerField(read_only=True)
    tasks_overdue = serializers.IntegerField(read_only=True)
    state_active = serializers.IntegerField(read_only=True)
    state_unresponsive = serializers.IntegerField(read_only=True)
    state_problem_acknowledged = serializers.IntegerField(read_only=True)
    state_maintenance_overdue = serializers.IntegerField(read_only=True)
    state_maintenance_scheduled = serializers.IntegerField(read_only=True)
    state_docked = serializers.IntegerField(read_only=True)
    state_decommissioned = serializers.IntegerField(read_only=True)


class DataStreamSerializer(InvenTreeModelSerializer):
    """Serializer for the DataStream model (streams of a deployment)."""

    class Meta:
        """Metaclass options."""

        model = DataStream
        fields = [
            'pk',
            'deployment',
            'key',
            'name',
            'essential',
            'expected_interval_minutes',
            'grace_minutes',
            'enabled',
            'last_seen',
            'state',
            'state_text',
        ]
        read_only_fields = ['last_seen', 'state']

    state_text = serializers.CharField(
        source='get_state_display', read_only=True, label=_('State')
    )

    def validate(self, data):
        """The deployment and key of an existing stream cannot change."""
        data = super().validate(data)

        if self.instance:
            for field in ['deployment', 'key']:
                if field in data and data[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({
                        field: _('This field cannot be changed')
                    })

        return data


class PositionFixSerializer(InvenTreeModelSerializer):
    """Serializer for the PositionFix model (position track)."""

    class Meta:
        """Metaclass options."""

        model = PositionFix
        fields = ['pk', 'timestamp', 'latitude', 'longitude', 'source']
        read_only_fields = fields


class DeploymentBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for the Deployment model."""

    class Meta:
        """Metaclass options."""

        model = Deployment
        fields = [
            'pk',
            'reference',
            'status',
            'health',
            'site',
            'device',
            'internal_state',
        ]
        read_only_fields = fields

    internal_state = serializers.CharField(
        source='device.fleet_link.state',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Manual State'),
    )


class AlertSerializer(
    CustomStatusSerializerMixin,
    FilterableSerializerMixin,
    DataImportExportSerializerMixin,
    InvenTreeCustomStatusSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the Alert model (alerts are raised by the system)."""

    class Meta:
        """Metaclass options."""

        model = Alert
        fields = [
            'pk',
            'reference',
            'status',
            'status_text',
            'status_custom_key',
            'alert_type',
            'severity',
            'severity_text',
            'message',
            'data',
            'deployment',
            'deployment_detail',
            'site',
            'site_detail',
            'stream',
            'stream_key',
            'device_serial',
            'opened_at',
            'acknowledged_at',
            'acknowledged_by',
            'acknowledged_by_detail',
            'resolved_at',
            'resolved_by',
            'resolved_by_detail',
            'resolution',
            'task',
            'dedupe_key',
        ]
        read_only_fields = fields

    deployment_detail = OptionalField(
        serializer_class=DeploymentBriefSerializer,
        serializer_kwargs={
            'source': 'deployment',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['deployment', 'deployment__device__fleet_link'],
    )

    site_detail = OptionalField(
        serializer_class=SiteBriefSerializer,
        serializer_kwargs={
            'source': 'site',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['site'],
    )

    stream_key = serializers.CharField(
        source='stream.key',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Stream'),
    )

    device_serial = serializers.CharField(
        source='deployment.device.serial',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Device'),
    )

    severity_text = serializers.CharField(
        source='get_severity_display', read_only=True, label=_('Severity')
    )

    acknowledged_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'acknowledged_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['acknowledged_by'],
    )

    resolved_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'resolved_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['resolved_by'],
    )

    @staticmethod
    def annotate_queryset(queryset):
        """Select the related objects used by the serializer."""
        return queryset.select_related(
            'stream', 'deployment__device', 'acknowledged_by', 'resolved_by'
        )


class AlertAcknowledgeSerializer(AlertActionSerializer):
    """Acknowledge an alert (no input)."""

    def save(self):
        """Acknowledge the alert."""
        self.target.acknowledge(user=self.user)


class AlertResolveSerializer(AlertActionSerializer):
    """Resolve an alert by hand."""

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=250, label=_('Note')
    )

    def save(self):
        """Resolve the alert."""
        self.target.resolve(user=self.user, note=self.validated_data.get('note', ''))


class DeploymentVerifySerializer(DeploymentActionSerializer):
    """Check that a deployed device reports fresh data (nothing is stored)."""

    since = serializers.DateTimeField(
        required=False,
        allow_null=True,
        label=_('Since'),
        help_text=_('Data must be newer than this (defaults to the verify window)'),
    )

    def save(self):
        """Fetch the live status of the device.

        Returns:
            The verify result
        """
        from fleet.services import monitoring

        if self.target.status != DeploymentStatus.DEPLOYED.value:
            raise serializers.ValidationError({
                'non_field_errors': [_('Only a deployed device can be verified')]
            })

        return monitoring.verify_now(
            self.target, since=self.validated_data.get('since')
        )


class VerifyStreamSerializer(serializers.Serializer):
    """Freshness of one stream in a verify result."""

    key = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    essential = serializers.BooleanField(read_only=True)
    last_seen = serializers.DateTimeField(read_only=True, allow_null=True)
    fresh = serializers.BooleanField(read_only=True)


class VerifyPositionSerializer(serializers.Serializer):
    """Position used in a verify result."""

    latitude = serializers.FloatField(read_only=True)
    longitude = serializers.FloatField(read_only=True)
    timestamp = serializers.DateTimeField(read_only=True, allow_null=True)


class VerifyResultSerializer(serializers.Serializer):
    """Result of a verify check (nothing is stored)."""

    checked_at = serializers.DateTimeField(read_only=True)
    since = serializers.DateTimeField(read_only=True)
    streams = VerifyStreamSerializer(many=True, read_only=True)
    position = VerifyPositionSerializer(read_only=True, allow_null=True)
    inside_geofence = serializers.BooleanField(read_only=True, allow_null=True)
    distance_m = serializers.FloatField(read_only=True, allow_null=True)
    passed = serializers.BooleanField(read_only=True)


class DeploymentMapSerializer(serializers.Serializer):
    """Compact deployment data for the fleet map."""

    pk = serializers.IntegerField(read_only=True)
    reference = serializers.CharField(read_only=True)
    status = serializers.IntegerField(read_only=True)
    site = serializers.IntegerField(read_only=True, allow_null=True)
    site_name = serializers.CharField(read_only=True, allow_null=True)
    site_reference = serializers.CharField(read_only=True, allow_null=True)
    device_serial = serializers.CharField(read_only=True, allow_null=True)
    latitude = serializers.FloatField(
        read_only=True, allow_null=True, help_text='Last position, else nominal'
    )
    longitude = serializers.FloatField(read_only=True, allow_null=True)
    nominal_latitude = serializers.FloatField(read_only=True, allow_null=True)
    nominal_longitude = serializers.FloatField(read_only=True, allow_null=True)
    last_latitude = serializers.FloatField(read_only=True, allow_null=True)
    last_longitude = serializers.FloatField(read_only=True, allow_null=True)
    last_position_at = serializers.DateTimeField(read_only=True, allow_null=True)
    distance_from_nominal_m = serializers.FloatField(read_only=True, allow_null=True)
    radius_m = serializers.IntegerField(read_only=True, allow_null=True)
    polygon = serializers.JSONField(read_only=True, allow_null=True)
    health = serializers.IntegerField(read_only=True, help_text='HealthStatus')
    last_contact = serializers.DateTimeField(read_only=True, allow_null=True)
    open_alert_count = serializers.IntegerField(read_only=True)
    device_state = serializers.CharField(read_only=True, allow_null=True)


# ---------------------------------------------------------------------------
# Maintenance (P3)
# ---------------------------------------------------------------------------


class ChecklistTemplateItemSerializer(InvenTreeModelSerializer):
    """Serializer for the ChecklistTemplateItem model."""

    class Meta:
        """Metaclass options."""

        model = ChecklistTemplateItem
        fields = [
            'pk',
            'device_type',
            'sequence',
            'text',
            'kind',
            'unit',
            'required',
            'task_type',
        ]


class KitTemplateLineSerializer(FilterableSerializerMixin, InvenTreeModelSerializer):
    """Serializer for the KitTemplateLine model."""

    class Meta:
        """Metaclass options."""

        model = KitTemplateLine
        fields = ['pk', 'device_type', 'part', 'part_detail', 'quantity', 'mode']

    part_detail = OptionalField(
        serializer_class=part_serializers.PartBriefSerializer,
        serializer_kwargs={'source': 'part', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['part'],
    )

    quantity = InvenTreeDecimalField(min_value=Decimal(0), label=_('Quantity'))


@register_importer()
class FaultCodeSerializer(DataImportExportSerializerMixin, InvenTreeModelSerializer):
    """Serializer for the FaultCode model."""

    class Meta:
        """Metaclass options."""

        model = FaultCode
        fields = ['pk', 'code', 'name', 'category', 'active']


class FaultCodeBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for the FaultCode model."""

    class Meta:
        """Metaclass options."""

        model = FaultCode
        fields = ['pk', 'code', 'name', 'category']
        read_only_fields = fields


class MaintenanceTaskSerializer(
    CustomStatusSerializerMixin,
    FilterableSerializerMixin,
    NotesFieldMixin,
    DataImportExportSerializerMixin,
    InvenTreeCustomStatusSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the MaintenanceTask model.

    Creating a task goes through maintenance.create_task: the status follows
    the scheduled date, and the deployment defaults to the device's active one.
    """

    class Meta:
        """Metaclass options."""

        model = MaintenanceTask
        fields = [
            'pk',
            'reference',
            'status',
            'status_text',
            'status_custom_key',
            'task_type',
            'description',
            'device',
            'device_detail',
            'deployment',
            'deployment_detail',
            'site',
            'site_detail',
            'display_name',
            'trip',
            'trip_reference',
            'due_date',
            'scheduled_date',
            'overdue',
            'started_at',
            'started_by',
            'started_by_detail',
            'completed_at',
            'completed_by',
            'completed_by_detail',
            'technicians',
            'technicians_detail',
            'labour_minutes',
            'as_found',
            'summary',
            'verification',
            'verification_override_reason',
            'follow_up_of',
            'follow_up_of_reference',
            'alerts',
            'checklist_count',
            'checklist_pending',
            'action_count',
            'barcode_hash',
            'notes',
        ]
        read_only_fields = [
            'status',
            'status_text',
            'status_custom_key',
            'trip',
            'started_at',
            'started_by',
            'completed_at',
            'completed_by',
            'verification',
            'verification_override_reason',
            'follow_up_of',
            'alerts',
        ]

    # Fields which cannot change once a task has started
    LOCKED_FIELDS = ['task_type', 'device', 'deployment']

    reference = serializers.CharField(required=False, label=_('Reference'))

    device = serializers.PrimaryKeyRelatedField(
        queryset=StockItem.objects.exclude(serial__isnull=True).exclude(serial=''),
        label=_('Device'),
        help_text=_('Serialized device worked on'),
    )

    device_detail = OptionalField(
        serializer_class=FleetDeviceBriefSerializer,
        serializer_kwargs={'source': 'device', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['device', 'device__part'],
    )

    deployment_detail = OptionalField(
        serializer_class=DeploymentBriefSerializer,
        serializer_kwargs={
            'source': 'deployment',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['deployment', 'deployment__device__fleet_link'],
    )

    site_detail = OptionalField(
        serializer_class=SiteBriefSerializer,
        serializer_kwargs={
            'source': 'site',
            'many': False,
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        prefetch_fields=['site'],
    )

    display_name = serializers.SerializerMethodField(label=_('Site / Device'))

    trip_reference = serializers.CharField(
        source='trip.reference',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Field Trip'),
    )

    overdue = serializers.SerializerMethodField(label=_('Overdue'))

    started_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'started_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['started_by'],
    )

    completed_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'completed_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['completed_by'],
    )

    technicians_detail = UserSerializer(
        source='technicians', many=True, read_only=True, label=_('Technicians')
    )

    barcode_hash = serializers.CharField(read_only=True)

    follow_up_of_reference = serializers.CharField(
        source='follow_up_of.reference',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Follow-up Of'),
    )

    checklist_count = serializers.IntegerField(
        read_only=True, label=_('Checklist Items')
    )

    checklist_pending = serializers.IntegerField(
        read_only=True, label=_('Required Items Pending')
    )

    action_count = serializers.IntegerField(read_only=True, label=_('Actions'))

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the checklist and action counts."""
        queryset = queryset.select_related(
            'device__part',
            'deployment__site',
            'site',
            'trip',
            'started_by',
            'completed_by',
            'follow_up_of',
        ).prefetch_related('technicians', 'alerts')

        return queryset.annotate(
            checklist_count=Count('checklist', distinct=True),
            checklist_pending=Count(
                'checklist',
                filter=Q(
                    checklist__required=True,
                    checklist__result=ChecklistResult.Result.PENDING,
                ),
                distinct=True,
            ),
            action_count=Count('actions', distinct=True),
        )

    def get_display_name(self, task) -> str:
        """The site nickname, else the device serial."""
        site = task.site or (task.deployment.site if task.deployment else None)

        if site is not None:
            return site.name

        return task.device.serial or str(task.device)

    def get_overdue(self, task) -> bool:
        """An open task whose due date has passed."""
        return bool(
            task.due_date
            and task.status in TaskStatusGroups.OPEN
            and task.due_date < current_date()
        )

    def validate_reference(self, reference):
        """The reference must match the reference pattern setting."""
        MaintenanceTask.validate_reference_field(reference)
        return reference

    def validate(self, data):
        """Check the device and deployment (which are fixed once started)."""
        data = super().validate(data)

        instance = self.instance

        if instance is not None and instance.status not in [
            TaskStatus.PROPOSED.value,
            TaskStatus.SCHEDULED.value,
        ]:
            for field in self.LOCKED_FIELDS:
                if field in data and data[field] != getattr(instance, field):
                    raise serializers.ValidationError({
                        field: _('This field cannot be changed once work has started')
                    })

        return data

    def create(self, validated_data):
        """Create the task through the maintenance service."""
        from fleet.services import maintenance

        technicians = validated_data.pop('technicians', [])
        task = maintenance.create_task(**validated_data)

        if technicians:
            task.technicians.set(technicians)

        return task

    def update(self, instance, validated_data):
        """Keep the status in step with the scheduled date."""
        from fleet.services import maintenance

        task = super().update(instance, validated_data)
        maintenance.update_schedule(task)

        return task


class ChecklistResultSerializer(InvenTreeModelSerializer):
    """Serializer for the ChecklistResult model (filled in during a task)."""

    class Meta:
        """Metaclass options."""

        model = ChecklistResult
        fields = [
            'pk',
            'task',
            'template_item',
            'sequence',
            'text',
            'kind',
            'unit',
            'required',
            'result',
            'value',
            'note',
            'fault_code',
            'fault_code_detail',
        ]
        read_only_fields = ['task', 'template_item', 'sequence', 'text', 'required']

    kind = serializers.CharField(
        source='template_item.kind',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Kind'),
    )

    unit = serializers.CharField(
        source='template_item.unit',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Unit'),
    )

    fault_code_detail = FaultCodeBriefSerializer(
        source='fault_code', read_only=True, allow_null=True, many=False
    )

    def validate_fault_code(self, fault_code):
        """Inactive fault codes cannot be selected."""
        if fault_code is not None and not fault_code.active:
            raise serializers.ValidationError(_('This fault code is not active'))

        return fault_code

    def validate(self, data):
        """The checklist can only be filled in while the task is in progress."""
        data = super().validate(data)

        if self.instance and self.instance.task.status != TaskStatus.IN_PROGRESS.value:
            raise serializers.ValidationError({
                'non_field_errors': [_('The task is not in progress')]
            })

        return data


class MaintenanceActionSerializer(FilterableSerializerMixin, InvenTreeModelSerializer):
    """Serializer for the MaintenanceAction model (read only)."""

    class Meta:
        """Metaclass options."""

        model = MaintenanceAction
        fields = [
            'pk',
            'task',
            'action',
            'component_out',
            'component_out_detail',
            'component_in',
            'component_in_detail',
            'part',
            'part_name',
            'quantity',
            'disposition',
            'destination',
            'destination_name',
            'fault_code',
            'fault_code_detail',
            'checklist_result',
            'note',
            'metadata',
            'created_at',
            'created_by',
            'created_by_detail',
        ]
        read_only_fields = fields

    component_out_detail = FleetDeviceBriefSerializer(
        source='component_out', read_only=True, allow_null=True, many=False
    )

    component_in_detail = FleetDeviceBriefSerializer(
        source='component_in', read_only=True, allow_null=True, many=False
    )

    part_name = serializers.CharField(
        source='part.full_name',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Part'),
    )

    quantity = InvenTreeDecimalField(read_only=True, allow_null=True)

    destination_name = serializers.CharField(
        source='destination.pathstring',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Destination'),
    )

    fault_code_detail = FaultCodeBriefSerializer(
        source='fault_code', read_only=True, allow_null=True, many=False
    )

    created_by_detail = OptionalField(
        serializer_class=UserSerializer,
        serializer_kwargs={
            'source': 'created_by',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['created_by'],
    )


class TaskStartSerializer(TaskActionSerializer):
    """Start a task (no input)."""

    def save(self):
        """Start the task."""
        self.target.start_task(user=self.user)


class TaskVerifySerializer(TaskActionSerializer):
    """Check that the device reports fresh data since the task started (no input)."""

    def save(self):
        """Verify the data (the result is stored on the task)."""
        from fleet.services import maintenance

        maintenance.verify(self.target, user=self.user)


class TaskComponentActionSerializer(TaskActionSerializer, ComponentActionSerializer):
    """A component action during a task.

    The same input as /api/stock/<pk>/components/, plus the fault code and the
    checklist item it addresses. The location of a removal is optional: it
    defaults to the trip "Removed" location, else the fleet workshop.
    """

    fault_code = serializers.PrimaryKeyRelatedField(
        queryset=FaultCode.objects.all(),
        required=False,
        allow_null=True,
        label=_('Fault Code'),
    )

    checklist_result = serializers.PrimaryKeyRelatedField(
        queryset=ChecklistResult.objects.all(),
        required=False,
        allow_null=True,
        label=_('Checklist Item'),
    )

    disable_stream = serializers.PrimaryKeyRelatedField(
        queryset=DataStream.objects.all(),
        required=False,
        allow_null=True,
        label=_('Stream no longer reported'),
        help_text=_(
            'Disable this data stream: the device no longer reports it without the component'
        ),
    )

    def validate(self, data):
        """Require the inputs of the action (the location is optional)."""
        action = data['action']
        required = []

        if action in ['remove', 'destroy', 'replace']:
            required.append('component')

        if action in ['add', 'replace']:
            required.append('stock_item')

        errors = {
            field: _('This field is required.')
            for field in required
            if field not in data
        }

        if errors:
            raise serializers.ValidationError(errors)

        return data

    def save(self):
        """Perform the component action."""
        from fleet.services import maintenance

        maintenance.component_action(self.target, self.user, self.validated_data)


class TaskConsumeSerializer(TaskActionSerializer):
    """Use a bulk consumable during a task."""

    stock_item = serializers.PrimaryKeyRelatedField(
        queryset=StockItem.objects.all(),
        label=_('Stock Item'),
        help_text=_('Consumable stock (from the trip kit when on a trip)'),
    )

    quantity = serializers.DecimalField(
        max_digits=15,
        decimal_places=5,
        min_value=Decimal('0.00001'),
        label=_('Quantity'),
    )

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, label=_('Note')
    )

    def save(self):
        """Take the stock."""
        from fleet.services import maintenance

        data = self.validated_data

        maintenance.consume(
            self.target,
            self.user,
            data['stock_item'],
            data['quantity'],
            note=data.get('note', ''),
        )


class TaskRepositionSerializer(TaskActionSerializer):
    """Move the nominal position of the deployed device."""

    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=-90, max_value=90, label=_('Latitude')
    )

    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        label=_('Longitude'),
    )

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, label=_('Note')
    )

    def save(self):
        """Set the new position."""
        from fleet.services import maintenance

        data = self.validated_data

        maintenance.reposition(
            self.target,
            self.user,
            data['latitude'],
            data['longitude'],
            note=data.get('note', ''),
        )


class TaskRecordActionSerializer(TaskActionSerializer):
    """Record an action without a stock change."""

    action = serializers.ChoiceField(
        choices=[
            (MaintenanceAction.Action.REPAIR, MaintenanceAction.Action.REPAIR.label),
            (MaintenanceAction.Action.CLEAN, MaintenanceAction.Action.CLEAN.label),
            (
                MaintenanceAction.Action.FIRMWARE,
                MaintenanceAction.Action.FIRMWARE.label,
            ),
            (MaintenanceAction.Action.OTHER, MaintenanceAction.Action.OTHER.label),
        ],
        label=_('Action'),
    )

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, label=_('Note')
    )

    fault_code = serializers.PrimaryKeyRelatedField(
        queryset=FaultCode.objects.all(),
        required=False,
        allow_null=True,
        label=_('Fault Code'),
    )

    checklist_result = serializers.PrimaryKeyRelatedField(
        queryset=ChecklistResult.objects.all(),
        required=False,
        allow_null=True,
        label=_('Checklist Item'),
    )

    firmware_version = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=50,
        label=_('Firmware Version'),
        help_text=_('New firmware version (firmware actions)'),
    )

    def save(self):
        """Record the action."""
        from fleet.services import maintenance

        data = self.validated_data

        maintenance.record_action(
            self.target,
            self.user,
            data['action'],
            note=data.get('note', ''),
            fault_code=data.get('fault_code'),
            checklist_result=data.get('checklist_result'),
            firmware_version=data.get('firmware_version', ''),
        )


class TaskCompleteSerializer(TaskActionSerializer):
    """Close a task."""

    labour_minutes = serializers.IntegerField(
        min_value=0,
        required=False,
        allow_null=True,
        label=_('Labour'),
        help_text=_('Minutes'),
    )

    summary = serializers.CharField(
        required=False, allow_blank=True, label=_('Summary')
    )

    override_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        label=_('Override Reason'),
        help_text=_(
            'Why the task is closed without passing the data check (creates a follow-up task)'
        ),
    )

    def save(self):
        """Complete the task."""
        data = self.validated_data

        self.target.complete_task(
            user=self.user,
            labour_minutes=data.get('labour_minutes'),
            summary=data.get('summary', ''),
            override_reason=data.get('override_reason', ''),
        )


class TaskCancelSerializer(TaskActionSerializer):
    """Cancel a task."""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=250, label=_('Reason')
    )

    def save(self):
        """Cancel the task."""
        self.target.cancel_task(
            user=self.user, reason=self.validated_data.get('reason', '')
        )


class AlertCreateTaskSerializer(AlertActionSerializer):
    """Create a corrective task for an alert."""

    scheduled_date = serializers.DateField(
        required=False,
        allow_null=True,
        label=_('Scheduled Date'),
        help_text=_('Leave blank to only propose the task'),
    )

    description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=250,
        label=_('Description'),
        help_text=_('Defaults to the alert message'),
    )

    trip = serializers.PrimaryKeyRelatedField(
        queryset=FieldTrip.objects.filter(status__in=TripStatusGroups.OPEN),
        required=False,
        allow_null=True,
        label=_('Field Trip'),
        help_text=_('Add the task to this trip'),
    )

    def save(self):
        """Create the task (and acknowledge the alert).

        Returns:
            The new task
        """
        from fleet.services import maintenance

        data = self.validated_data

        return maintenance.create_from_alert(
            self.target,
            user=self.user,
            scheduled_date=data.get('scheduled_date'),
            trip=data.get('trip'),
            description=data.get('description', ''),
        )


class TaskDeploySerializer(TaskActionSerializer, DeployFieldsSerializer):
    """Deploy the device of a deployment (or swap) task."""

    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, label=_('Note')
    )

    def save(self):
        """Deploy the device (a swap also recovers the replaced device)."""
        from fleet.services import maintenance

        data = self.validated_data

        maintenance.deploy(
            self.target,
            user=self.user,
            latitude=data.get('latitude'),
            longitude=data.get('longitude'),
            depth_m=data.get('depth_m'),
            deployed_at=data.get('deployed_at'),
            note=data.get('note', ''),
        )


# ---------------------------------------------------------------------------
# Field trips
# ---------------------------------------------------------------------------


class FieldTripBriefSerializer(InvenTreeModelSerializer):
    """Brief serializer for the FieldTrip model."""

    class Meta:
        """Metaclass options."""

        model = FieldTrip
        fields = ['pk', 'reference', 'title', 'status', 'start_date', 'end_date']
        read_only_fields = fields


class FieldTripSerializer(
    CustomStatusSerializerMixin,
    FilterableSerializerMixin,
    NotesFieldMixin,
    DataImportExportSerializerMixin,
    InvenTreeCustomStatusSerializerMixin,
    InvenTreeModelSerializer,
):
    """Serializer for the FieldTrip model.

    Creating a trip goes through trips.create_trip, which creates the kit
    location; tasks and ready deployments can be added at once (plan selection).
    """

    class Meta:
        """Metaclass options."""

        model = FieldTrip
        fields = [
            'pk',
            'reference',
            'status',
            'status_text',
            'status_custom_key',
            'title',
            'start_date',
            'end_date',
            'vessel',
            'team',
            'team_detail',
            'responsible',
            'responsible_detail',
            'kit_location',
            'kit_location_name',
            'removed_location',
            'task_count',
            'open_task_count',
            'completed_task_count',
            'kit_line_count',
            'tasks',
            'deployments',
            'barcode_hash',
            'notes',
        ]
        read_only_fields = [
            'status',
            'status_text',
            'status_custom_key',
            'kit_location',
        ]

    reference = serializers.CharField(required=False, label=_('Reference'))

    team_detail = UserSerializer(
        source='team', many=True, read_only=True, label=_('Team')
    )

    responsible_detail = OptionalField(
        serializer_class=OwnerSerializer,
        serializer_kwargs={
            'source': 'responsible',
            'read_only': True,
            'allow_null': True,
        },
        default_include=True,
        filter_name='user_detail',
        prefetch_fields=['responsible'],
    )

    barcode_hash = serializers.CharField(read_only=True)

    kit_location_name = serializers.CharField(
        source='kit_location.pathstring',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Kit Location'),
    )

    removed_location = serializers.SerializerMethodField(label=_('Removed Location'))

    task_count = serializers.IntegerField(read_only=True, label=_('Tasks'))

    open_task_count = serializers.IntegerField(read_only=True, label=_('Open Tasks'))

    completed_task_count = serializers.IntegerField(
        read_only=True, label=_('Completed Tasks')
    )

    kit_line_count = serializers.IntegerField(read_only=True, label=_('Kit Lines'))

    tasks = serializers.PrimaryKeyRelatedField(
        queryset=MaintenanceTask.objects.filter(
            status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value]
        ),
        many=True,
        required=False,
        write_only=True,
        label=_('Tasks'),
        help_text=_('Tasks to add to the new trip'),
    )

    deployments = serializers.PrimaryKeyRelatedField(
        queryset=Deployment.objects.filter(
            status__in=DeploymentStatusGroups.DEPLOYABLE
        ),
        many=True,
        required=False,
        write_only=True,
        label=_('Deployments'),
        help_text=_('Ready deployments to deploy on the new trip'),
    )

    @staticmethod
    def annotate_queryset(queryset):
        """Annotate the task and kit line counts."""
        from fleet.services.maintenance import REMOVED_LOCATION_NAME

        queryset = queryset.select_related(
            'responsible', 'kit_location'
        ).prefetch_related('team')

        removed = StockLocation.objects.filter(
            parent=OuterRef('kit_location'), name=REMOVED_LOCATION_NAME
        )

        return queryset.annotate(
            removed_location_pk=Subquery(removed.values('pk')[:1]),
            task_count=Count('tasks', distinct=True),
            open_task_count=Count(
                'tasks',
                filter=Q(tasks__status__in=TaskStatusGroups.OPEN),
                distinct=True,
            ),
            completed_task_count=Count(
                'tasks',
                filter=Q(tasks__status=TaskStatus.COMPLETED.value),
                distinct=True,
            ),
            kit_line_count=Count('kit_lines', distinct=True),
        )

    def get_removed_location(self, trip) -> int | None:
        """The "Removed" child of the kit location (annotated on lists)."""
        if hasattr(trip, 'removed_location_pk'):
            return trip.removed_location_pk

        from fleet.services.maintenance import REMOVED_LOCATION_NAME

        if trip.kit_location_id is None:
            return None

        location = StockLocation.objects.filter(
            parent=trip.kit_location_id, name=REMOVED_LOCATION_NAME
        ).first()

        return location.pk if location else None

    def skip_create_fields(self):
        """Many-to-many and write-only fields are not set on the test instance."""
        return [*super().skip_create_fields(), 'team', 'tasks', 'deployments']

    def validate_reference(self, reference):
        """The reference must match the reference pattern setting."""
        FieldTrip.validate_reference_field(reference)
        return reference

    def validate(self, data):
        """Tasks are only added when the trip is created."""
        data = super().validate(data)

        if self.instance is not None and (data.get('tasks') or data.get('deployments')):
            raise serializers.ValidationError({
                'tasks': _('Use "Add tasks" to add tasks to an existing trip')
            })

        return data

    def create(self, validated_data):
        """Create the trip (and its kit location) through the trips service."""
        from fleet.services import trips

        team = validated_data.pop('team', [])

        return trips.create_trip(team=team, **validated_data)

    def update(self, instance, validated_data):
        """The task and deployment lists are only used on creation."""
        validated_data.pop('tasks', None)
        validated_data.pop('deployments', None)

        return super().update(instance, validated_data)


class TripKitLineSerializer(FilterableSerializerMixin, InvenTreeModelSerializer):
    """Serializer for the TripKitLine model.

    Lines added by hand are MANUAL; the others come from the kit suggestion.
    """

    class Meta:
        """Metaclass options."""

        model = TripKitLine
        fields = [
            'pk',
            'trip',
            'part',
            'part_detail',
            'quantity_planned',
            'source',
            'note',
            'stock_item',
            'serial',
            'task',
            'task_reference',
        ]
        read_only_fields = ['source', 'stock_item', 'task']

    part_detail = OptionalField(
        serializer_class=part_serializers.PartBriefSerializer,
        serializer_kwargs={'source': 'part', 'many': False, 'read_only': True},
        default_include=True,
        prefetch_fields=['part'],
    )

    quantity_planned = InvenTreeDecimalField(
        min_value=Decimal(0), label=_('Planned Quantity')
    )

    serial = serializers.CharField(
        source='stock_item.serial',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Serial'),
    )

    task_reference = serializers.CharField(
        source='task.reference',
        read_only=True,
        allow_null=True,
        default=None,
        label=_('Task'),
    )

    def validate(self, data):
        """Kit lines can only change while the trip is open."""
        data = super().validate(data)

        instance = self.instance
        trip = data.get('trip', getattr(instance, 'trip', None))

        if trip is not None and trip.status not in TripStatusGroups.OPEN:
            raise serializers.ValidationError({
                'trip': _('The kit of a closed trip cannot be changed')
            })

        if instance is not None and 'trip' in data and data['trip'] != instance.trip:
            raise serializers.ValidationError({
                'trip': _('A kit line cannot move to another trip')
            })

        if (
            instance is not None
            and instance.stock_item_id
            and 'part' in data
            and data['part'] != instance.part
        ):
            raise serializers.ValidationError({
                'part': _('The part of a device line cannot change')
            })

        return data

    def create(self, validated_data):
        """Lines added by hand are MANUAL."""
        validated_data['source'] = TripKitLine.Source.MANUAL
        return super().create(validated_data)


class KitStockSerializer(serializers.Serializer):
    """A stock item in a trip kit (or in its "Removed" location)."""

    pk = serializers.IntegerField()
    part = serializers.IntegerField(source='part_id')
    part_name = serializers.CharField(source='part.full_name')
    serial = serializers.CharField(allow_null=True)
    batch = serializers.CharField(allow_null=True)
    quantity = serializers.FloatField()
    status = serializers.IntegerField()
    status_text = serializers.CharField(source='get_status_display')
    location = serializers.IntegerField(source='location_id', allow_null=True)
    location_name = serializers.CharField(
        source='location.pathstring', allow_null=True, default=None
    )
    default_location = serializers.SerializerMethodField()

    def get_default_location(self, item) -> int | None:
        """Where the item returns to by default (the part default location)."""
        location = item.part.get_default_location()
        return location.pk if location else None


class KitLineAvailabilitySerializer(serializers.Serializer):
    """A trip kit line with its availability."""

    line = TripKitLineSerializer()
    available = serializers.FloatField(
        label=_('Available'), help_text=_('In stock outside the trip kits')
    )
    in_kit = serializers.FloatField(label=_('In Kit'))
    missing = serializers.FloatField(label=_('Missing'))


class TripKitSerializer(serializers.Serializer):
    """The kit of a trip: planned lines, the kit contents and removed components."""

    trip = serializers.IntegerField()
    kit_location = serializers.IntegerField(allow_null=True)
    removed_location = serializers.IntegerField(allow_null=True)
    lines = KitLineAvailabilitySerializer(many=True)
    contents = KitStockSerializer(many=True)
    removed = KitStockSerializer(many=True)


class TripAddTasksSerializer(TripActionSerializer):
    """Add tasks, and ready deployments (as deployment tasks), to a trip."""

    tasks = serializers.PrimaryKeyRelatedField(
        queryset=MaintenanceTask.objects.all(),
        many=True,
        required=False,
        label=_('Tasks'),
    )

    deployments = serializers.PrimaryKeyRelatedField(
        queryset=Deployment.objects.all(),
        many=True,
        required=False,
        label=_('Deployments'),
        help_text=_('Ready deployments: each gets a deployment (or swap) task'),
    )

    def validate(self, data):
        """Something must be selected."""
        if not data.get('tasks') and not data.get('deployments'):
            raise serializers.ValidationError({
                'non_field_errors': [_('Select tasks or deployments to add')]
            })

        return data

    def save(self):
        """Schedule the tasks on the trip."""
        from fleet.services import trips

        trips.add_tasks(
            self.target,
            tasks=self.validated_data.get('tasks', []),
            deployments=self.validated_data.get('deployments', []),
        )


class TripRemoveTaskSerializer(TripActionSerializer):
    """Take a task which has not started off the trip."""

    task = serializers.PrimaryKeyRelatedField(
        queryset=MaintenanceTask.objects.all(), label=_('Task')
    )

    def save(self):
        """Unschedule the task."""
        from fleet.services import trips

        trips.remove_task(self.target, self.validated_data['task'])


class TripSuggestKitSerializer(TripActionSerializer):
    """Regenerate the suggested kit lines (no input)."""

    def save(self):
        """Suggest the kit."""
        from fleet.services import planning

        if self.target.status not in TripStatusGroups.OPEN:
            raise serializers.ValidationError({
                'non_field_errors': [_('The kit of a closed trip cannot be changed')]
            })

        planning.suggest_kit(self.target)


class TripStartSerializer(TripActionSerializer):
    """Start a trip (no input)."""

    def save(self):
        """Start the trip."""
        self.target.start_trip(user=self.user)


class TripPrepareKitItemSerializer(serializers.Serializer):
    """A stock item to move into the trip kit."""

    stock_item = serializers.PrimaryKeyRelatedField(
        queryset=StockItem.objects.all(), label=_('Stock Item')
    )

    quantity = serializers.DecimalField(
        max_digits=15,
        decimal_places=5,
        min_value=Decimal('0.00001'),
        required=False,
        allow_null=True,
        label=_('Quantity'),
        help_text=_('Defaults to the whole stock item'),
    )


class TripPrepareKitSerializer(TripActionSerializer):
    """Move stock into the trip kit location."""

    items = TripPrepareKitItemSerializer(many=True, label=_('Items'))

    def validate_items(self, items):
        """At least one item."""
        if not items:
            raise serializers.ValidationError(_('Select the stock to take'))

        return items

    def save(self):
        """Move the stock."""
        self.target.prepare_kit(self.validated_data['items'], user=self.user)


class TripReconcileReturnSerializer(serializers.Serializer):
    """Where a leftover or removed stock item goes."""

    stock_item = serializers.PrimaryKeyRelatedField(
        queryset=StockItem.objects.all(), label=_('Stock Item')
    )

    location = serializers.PrimaryKeyRelatedField(
        queryset=StockLocation.objects.filter(structural=False),
        allow_null=True,
        label=_('Location'),
    )


class TripReconcileSerializer(TripActionSerializer):
    """Return the kit to stock, and close the trip."""

    returns = TripReconcileReturnSerializer(
        many=True,
        required=False,
        label=_('Returns'),
        help_text=_(
            'Destination per stock item (default: the part default location; removed components go to the workshop)'
        ),
    )

    def save(self):
        """Reconcile the trip; the response includes the result."""
        result = self.target.reconcile_trip(
            user=self.user, returns=self.validated_data.get('returns', [])
        )

        return {'reconcile': TripReconcileResultSerializer(result).data}


class TripReconcileResultSerializer(serializers.Serializer):
    """The result of a reconcile."""

    returned = serializers.IntegerField()
    to_workshop = serializers.IntegerField()
    released = serializers.IntegerField()
    missing_location = serializers.ListField(child=serializers.CharField())
    open_tasks = serializers.ListField(child=serializers.CharField())
    closed = serializers.BooleanField()


class TripCancelSerializer(TripActionSerializer):
    """Cancel a trip which has not started."""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=250, label=_('Reason')
    )

    def save(self):
        """Cancel the trip."""
        self.target.cancel_trip(
            user=self.user, reason=self.validated_data.get('reason', '')
        )
