"""Database models for the fleet app.

The fleet app tracks where serialized devices are deployed, their live state
(as reported by the data platform), alerts, preventive and corrective
maintenance, and the field trips which carry out that maintenance.

Physical items remain in the stock app: fleet models only reference them.
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import generic.states
import InvenTree.helpers
import InvenTree.models
import report.mixins
import stock.models
import users.models
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
from fleet.validators import (
    generate_next_alert_reference,
    generate_next_deployment_reference,
    generate_next_site_reference,
    generate_next_task_reference,
    generate_next_trip_reference,
    validate_alert_reference,
    validate_deployment_reference,
    validate_geofence_polygon,
    validate_site_reference,
    validate_task_reference,
    validate_trip_reference,
)
from generic.states import StateTransitionMixin, StatusCodeMixin


class Coverage(models.TextChoices):
    """Service coverage of a site or deployment."""

    FULL = 'FULL', _('Full service')
    NO_SERVICE = 'NO_SERVICE', _('Monitored only, no service')
    THIRD_PARTY = 'THIRD_PARTY', _('Serviced by a third party')


class InternalState(models.TextChoices):
    """Internal state of a physical device, set by a person (see DeviceState)."""

    NONE = '', _('None')
    PROBLEM_ACKNOWLEDGED = 'PROBLEM_ACKNOWLEDGED', _('Problem Acknowledged')
    DOCKED = 'DOCKED', _('Docked')
    DECOMMISSIONED = 'DECOMMISSIONED', _('Decommissioned')


class DeviceState(models.TextChoices):
    """Effective state of a device, in priority order (the first match wins).

    DECOMMISSIONED, DOCKED and PROBLEM_ACKNOWLEDGED are set by a person
    (DeviceLink.state); the others are worked out from the deployment.
    """

    DECOMMISSIONED = 'DECOMMISSIONED', _('Decommissioned')
    DOCKED = 'DOCKED', _('Docked')
    MAINTENANCE_SCHEDULED = 'MAINTENANCE_SCHEDULED', _('Maintenance Scheduled')
    MAINTENANCE_OVERDUE = 'MAINTENANCE_OVERDUE', _('Maintenance Overdue')
    PROBLEM_ACKNOWLEDGED = 'PROBLEM_ACKNOWLEDGED', _('Problem Acknowledged')
    UNRESPONSIVE = 'UNRESPONSIVE', _('Unresponsive')
    ACTIVE = 'ACTIVE', _('Active')


class TaskType(models.TextChoices):
    """Type of maintenance task."""

    PREVENTIVE = 'PREVENTIVE', _('Preventive')
    CORRECTIVE = 'CORRECTIVE', _('Corrective')
    DEPLOYMENT = 'DEPLOYMENT', _('Deployment')
    RECOVERY = 'RECOVERY', _('Recovery')
    SWAP = 'SWAP', _('Swap')
    INSPECTION = 'INSPECTION', _('Inspection')


class KitMode(models.TextChoices):
    """Whether a kit part is always needed, or only likely to be needed."""

    ALWAYS = 'ALWAYS', _('Always')
    LIKELY = 'LIKELY', _('Likely')


def latitude_field(verbose_name, **kwargs):
    """Return a decimal field for a latitude value."""
    return models.DecimalField(
        verbose_name=verbose_name,
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        **kwargs,
    )


def longitude_field(verbose_name, **kwargs):
    """Return a decimal field for a longitude value."""
    return models.DecimalField(
        verbose_name=verbose_name,
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        **kwargs,
    )


def depth_field(**kwargs):
    """Return a decimal field for a water depth, in metres."""
    return models.DecimalField(
        verbose_name=_('Depth'),
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text=_('Water depth (m)'),
        **kwargs,
    )


def geofence_radius_field(**kwargs):
    """Return an integer field for a geofence radius, in metres."""
    return models.PositiveIntegerField(
        verbose_name=_('Geofence Radius'),
        blank=True,
        null=True,
        help_text=_('Geofence radius (m). Leave blank to use the default'),
        **kwargs,
    )


def geofence_polygon_field(**kwargs):
    """Return a JSON field for a geofence polygon."""
    return models.JSONField(
        verbose_name=_('Geofence Polygon'),
        blank=True,
        null=True,
        validators=[validate_geofence_polygon],
        help_text=_('Optional polygon, as a list of [latitude, longitude] points'),
        **kwargs,
    )


class FleetDeviceType(
    InvenTree.models.InvenTreeNotesMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """Marks a Part as a fleet device, and holds its maintenance rules.

    Attributes:
        part: The (assembly, trackable) part which is a fleet device
        pm_interval_days: Default preventive maintenance interval
        verify_window_minutes: How long "Verify data" waits for fresh data
        active: Inactive device types do not enter the deployment pipeline
    """

    class Meta:
        """Metaclass options for the FleetDeviceType model."""

        verbose_name = _('Fleet Device Type')
        verbose_name_plural = _('Fleet Device Types')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the FleetDeviceType model."""
        return reverse('api-fleet-device-type-list')

    def __str__(self):
        """String representation of a FleetDeviceType."""
        return str(self.part.full_name)

    def get_absolute_url(self):
        """Return the web URL associated with this FleetDeviceType."""
        return InvenTree.helpers.pui_url(f'/fleet/device-type/{self.pk}')

    part = models.OneToOneField(
        'part.Part',
        on_delete=models.CASCADE,
        related_name='fleet_device_type',
        verbose_name=_('Part'),
        help_text=_('Part which is a fleet device'),
        limit_choices_to={'assembly': True, 'trackable': True},
    )

    pm_interval_days = models.PositiveIntegerField(
        default=180,
        validators=[MinValueValidator(1)],
        verbose_name=_('PM Interval'),
        help_text=_('Preventive maintenance interval (days)'),
    )

    verify_window_minutes = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        verbose_name=_('Verify Window'),
        help_text=_('How long to wait for fresh data when verifying (minutes)'),
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('Active'),
        help_text=_('Inactive device types do not enter the deployment pipeline'),
    )


class StreamTemplate(InvenTree.models.InvenTreeModel):
    """A default data stream for a device type.

    Attributes:
        device_type: The device type which reports this stream
        key: Stream identifier on the data platform (e.g. "hydrophone")
        name: Display name
        essential: Whether the device is unhealthy without this stream
        expected_interval_minutes: How often data is expected
        grace_minutes: Extra time allowed before the stream is late
    """

    class Meta:
        """Metaclass options for the StreamTemplate model."""

        verbose_name = _('Stream Template')
        verbose_name_plural = _('Stream Templates')
        unique_together = [('device_type', 'key')]

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-stream-template-list')

    def __str__(self):
        """String representation of a StreamTemplate."""
        return f'{self.device_type} - {self.key}'

    device_type = models.ForeignKey(
        FleetDeviceType,
        on_delete=models.CASCADE,
        related_name='stream_templates',
        verbose_name=_('Device Type'),
    )

    key = models.CharField(
        max_length=100,
        verbose_name=_('Key'),
        help_text=_('Stream identifier on the data platform'),
    )

    name = models.CharField(max_length=100, blank=True, verbose_name=_('Name'))

    essential = models.BooleanField(
        default=False,
        verbose_name=_('Essential'),
        help_text=_('Device is critical when this stream is missing'),
    )

    expected_interval_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        verbose_name=_('Expected Interval'),
        help_text=_('How often data is expected (minutes)'),
    )

    grace_minutes = models.PositiveIntegerField(
        default=15,
        verbose_name=_('Grace Period'),
        help_text=_('Extra time allowed before the stream is late (minutes)'),
    )


class ChecklistTemplateItem(InvenTree.models.InvenTreeModel):
    """A preventive maintenance checklist item for a device type.

    Attributes:
        device_type: The device type this checklist item applies to
        sequence: Display order
        text: The check to perform
        kind: Type of result (check, measurement or photo)
        unit: Unit for a measurement
        required: Whether the item must be answered before a task can be closed
        task_type: Type of task which uses this item (blank: every task type)
    """

    class Kind(models.TextChoices):
        """Type of checklist item."""

        CHECK = 'CHECK', _('Check')
        MEASUREMENT = 'MEASUREMENT', _('Measurement')
        PHOTO = 'PHOTO', _('Photo')

    class Meta:
        """Metaclass options for the ChecklistTemplateItem model."""

        verbose_name = _('Checklist Template Item')
        verbose_name_plural = _('Checklist Template Items')
        ordering = ['device_type', 'sequence', 'pk']

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-checklist-item-list')

    def __str__(self):
        """String representation of a ChecklistTemplateItem."""
        return self.text

    device_type = models.ForeignKey(
        FleetDeviceType,
        on_delete=models.CASCADE,
        related_name='checklist_items',
        verbose_name=_('Device Type'),
    )

    sequence = models.PositiveIntegerField(default=0, verbose_name=_('Sequence'))

    text = models.CharField(max_length=250, verbose_name=_('Text'))

    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.CHECK, verbose_name=_('Kind')
    )

    unit = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Unit'),
        help_text=_('Unit for a measurement'),
    )

    required = models.BooleanField(default=True, verbose_name=_('Required'))

    task_type = models.CharField(
        max_length=20,
        choices=TaskType.choices,
        blank=True,
        default=TaskType.PREVENTIVE,
        verbose_name=_('Task Type'),
        help_text=_('Type of task which uses this item (blank: every task type)'),
    )


class KitTemplateLine(InvenTree.models.InvenTreeModel):
    """A part typically taken on a preventive maintenance visit for a device type.

    Attributes:
        device_type: The device type this kit line applies to
        part: The part to take
        quantity: Quantity to take per device
        mode: Whether the part is always needed or only likely
    """

    class Meta:
        """Metaclass options for the KitTemplateLine model."""

        verbose_name = _('Kit Template Line')
        verbose_name_plural = _('Kit Template Lines')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-kit-line-list')

    def __str__(self):
        """String representation of a KitTemplateLine."""
        return f'{self.device_type} - {self.part}'

    device_type = models.ForeignKey(
        FleetDeviceType,
        on_delete=models.CASCADE,
        related_name='kit_lines',
        verbose_name=_('Device Type'),
    )

    part = models.ForeignKey(
        'part.Part',
        on_delete=models.CASCADE,
        related_name='fleet_kit_lines',
        verbose_name=_('Part'),
    )

    quantity = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        default=1,
        validators=[MinValueValidator(0)],
        verbose_name=_('Quantity'),
    )

    mode = models.CharField(
        max_length=20,
        choices=KitMode.choices,
        default=KitMode.ALWAYS,
        verbose_name=_('Mode'),
    )


class FaultCode(InvenTree.models.InvenTreeModel):
    """A standard fault, recorded against checklist results and maintenance actions.

    Attributes:
        code: Unique code (e.g. WATER_INGRESS)
        name: Display name
        category: Fault category
        active: Inactive codes cannot be selected
    """

    class Category(models.TextChoices):
        """Fault categories."""

        POWER = 'POWER', _('Power')
        SENSOR = 'SENSOR', _('Sensor')
        ENCLOSURE = 'ENCLOSURE', _('Enclosure')
        CONNECTOR_CABLE = 'CONNECTOR_CABLE', _('Connector / cable')
        MOORING = 'MOORING', _('Mooring')
        FOULING = 'FOULING', _('Fouling')
        FIRMWARE_COMMS = 'FIRMWARE_COMMS', _('Firmware / comms')
        EXTERNAL_DAMAGE = 'EXTERNAL_DAMAGE', _('External damage')
        OTHER = 'OTHER', _('Other')

    class Meta:
        """Metaclass options for the FaultCode model."""

        verbose_name = _('Fault Code')
        verbose_name_plural = _('Fault Codes')
        ordering = ['code']

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the FaultCode model."""
        return reverse('api-fleet-fault-code-list')

    def __str__(self):
        """String representation of a FaultCode."""
        return self.name

    code = models.CharField(max_length=50, unique=True, verbose_name=_('Code'))

    name = models.CharField(max_length=100, verbose_name=_('Name'))

    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
        verbose_name=_('Category'),
    )

    active = models.BooleanField(default=True, verbose_name=_('Active'))


class Site(
    InvenTree.models.InvenTreeAttachmentMixin,
    InvenTree.models.InvenTreeNotesMixin,
    InvenTree.models.ReferenceIndexingMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """A physical location where devices are deployed.

    The name is the nickname shown in the app (e.g. "Algarve #1"). It stays
    with the location when the device at that location is replaced.

    Attributes:
        reference: Unique reference (e.g. ST-001)
        name: Nickname of the site
        latitude: Nominal latitude
        longitude: Nominal longitude
        depth_m: Water depth (m)
        coverage: Service coverage of devices at this site
        client: Customer company which the data is produced for
        project: Project name
        pm_interval_override_days: Overrides the device type PM interval
        geofence_radius_m: Geofence radius (m), or null to use the default
        geofence_polygon: Optional geofence polygon
        active: Inactive sites are hidden from planning
    """

    REFERENCE_PATTERN_SETTING = 'FLEET_SITE_REFERENCE_PATTERN'

    class Meta:
        """Metaclass options for the Site model."""

        verbose_name = _('Site')
        verbose_name_plural = _('Sites')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the Site model."""
        return reverse('api-fleet-site-list')

    @classmethod
    def api_defaults(cls, request=None):
        """Return default values for this model when issuing an API OPTIONS request."""
        return {'reference': generate_next_site_reference()}

    def save(self, *args, **kwargs):
        """Custom save method for the Site model."""
        self.reference_int = self.validate_reference_field(self.reference)
        super().save(*args, **kwargs)

    def __str__(self):
        """String representation of a Site."""
        return f'{self.reference} - {self.name}'

    def get_absolute_url(self):
        """Return the web URL associated with this Site."""
        return InvenTree.helpers.pui_url(f'/fleet/site/{self.pk}')

    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Site Reference'),
        default=generate_next_site_reference,
        validators=[validate_site_reference],
    )

    name = models.CharField(
        max_length=100,
        verbose_name=_('Name'),
        help_text=_('Nickname shown in the app (e.g. "Algarve #1")'),
    )

    latitude = latitude_field(_('Latitude'), blank=True, null=True)

    longitude = longitude_field(_('Longitude'), blank=True, null=True)

    depth_m = depth_field()

    coverage = models.CharField(
        max_length=20,
        choices=Coverage.choices,
        default=Coverage.FULL,
        verbose_name=_('Coverage'),
        help_text=_('Service coverage of devices at this site'),
    )

    client = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_sites',
        limit_choices_to={'is_customer': True},
        verbose_name=_('Client'),
        help_text=_('Customer which the data is produced for'),
    )

    project = models.CharField(max_length=100, blank=True, verbose_name=_('Project'))

    pm_interval_override_days = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
        verbose_name=_('PM Interval Override'),
        help_text=_('Overrides the device type PM interval (days)'),
    )

    geofence_radius_m = geofence_radius_field()

    geofence_polygon = geofence_polygon_field()

    active = models.BooleanField(default=True, verbose_name=_('Active'))


class DeviceLink(InvenTree.models.MetadataMixin, InvenTree.models.InvenTreeModel):
    """Identity of a physical device on the data platform.

    Attributes:
        stock_item: The serialized device
        platform_id: Device identifier used by the data platform API
        firmware_version: Last known firmware version
        comment: Free text comment
        state: Internal state set by a person (problem acknowledged, docked,
            decommissioned); it belongs to the device, not to one deployment
        state_note: Why the state was set
        state_changed_at: When the state was last changed
        state_changed_by: Who last changed the state
    """

    class Meta:
        """Metaclass options for the DeviceLink model."""

        verbose_name = _('Device Link')
        verbose_name_plural = _('Device Links')
        constraints = [
            # Blank platform ids are allowed (and flagged) until the link is configured
            models.UniqueConstraint(
                fields=['platform_id'],
                condition=~Q(platform_id=''),
                name='fleet_devicelink_unique_platform_id',
            )
        ]

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the DeviceLink model."""
        return reverse('api-fleet-device-link-list')

    def __str__(self):
        """String representation of a DeviceLink."""
        return f'{self.stock_item} - {self.platform_id}'

    stock_item = models.OneToOneField(
        'stock.StockItem',
        on_delete=models.CASCADE,
        related_name='fleet_link',
        verbose_name=_('Stock Item'),
    )

    platform_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Platform ID'),
        help_text=_('Device identifier on the data platform'),
    )

    firmware_version = models.CharField(
        max_length=50, blank=True, verbose_name=_('Firmware Version')
    )

    comment = models.CharField(max_length=250, blank=True, verbose_name=_('Comment'))

    state = models.CharField(
        max_length=30,
        choices=InternalState.choices,
        blank=True,
        default=InternalState.NONE,
        verbose_name=_('State'),
        help_text=_('Internal state of the device'),
    )

    state_note = models.CharField(
        max_length=250, blank=True, verbose_name=_('State Note')
    )

    state_changed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('State Changed')
    )

    state_changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name=_('State Changed By'),
    )


class DeploymentReportContext(report.mixins.BaseReportContext):
    """Context for the Deployment model.

    Attributes:
        deployment: The Deployment object
        reference: The reference of the deployment
        title: The title of the report
        site: The site of the deployment
        device: The deployed stock item
        streams: Query set of DataStream objects
        alerts: Query set of Alert objects
        tasks: Query set of MaintenanceTask objects
    """

    deployment: 'Deployment'
    reference: str
    title: str
    site: 'Site | None'
    device: 'stock.models.StockItem | None'
    streams: report.mixins.QuerySet['DataStream']
    alerts: report.mixins.QuerySet['Alert']
    tasks: report.mixins.QuerySet['MaintenanceTask']


class Deployment(
    report.mixins.InvenTreeReportMixin,
    InvenTree.models.InvenTreeAttachmentMixin,
    InvenTree.models.InvenTreeBarcodeMixin,
    InvenTree.models.InvenTreeNotesMixin,
    InvenTree.models.ReferenceIndexingMixin,
    StateTransitionMixin,
    StatusCodeMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """A device deployed (or planned to be deployed) at a site.

    A deployment enters the pipeline when its build order is issued, or when it
    is planned manually. It holds a cache of the live device state, which is
    written by the monitoring service only.

    Attributes:
        reference: Unique reference (e.g. DP-0001)
        creation_date: When the deployment entered the pipeline
        status: Deployment status
        device_type: Type of device to deploy
        build: The build order which produces the device
        device: The serialized device, once known
        site: Where the device is (or will be) deployed
        deployment_type: New station, replacement or temporary
        replaces: For a replacement, the deployment which it ends
        client: Customer company (defaults from the site)
        coverage: Service coverage (copied from the site)
        target_date: Planned deployment date
        deployed_at: When the device was deployed
        recovered_at: When the device was recovered
        latitude: Nominal latitude, recorded at deployment
        longitude: Nominal longitude, recorded at deployment
        depth_m: Water depth (m)
        geofence_radius_m: Geofence radius (m), copied from the site
        geofence_polygon: Geofence polygon, copied from the site
        next_pm_date: Next preventive maintenance date
        next_pm_manual: Whether next_pm_date was set by hand
        health: Overall health (cached)
        last_contact: Last time any stream reported (cached)
        last_latitude: Last reported latitude (cached)
        last_longitude: Last reported longitude (cached)
        last_position_at: Time of the last reported position (cached)
        distance_from_nominal_m: Distance from the nominal position (cached)
        last_polled_at: Last time the data platform was polled (cached)
    """

    REFERENCE_PATTERN_SETTING = 'FLEET_DEPLOYMENT_REFERENCE_PATTERN'
    STATUS_CLASS = DeploymentStatus

    class DeploymentType(models.TextChoices):
        """Type of deployment."""

        NEW_STATION = 'NEW_STATION', _('New station')
        REPLACEMENT = 'REPLACEMENT', _('Replacement')
        TEMPORARY = 'TEMPORARY', _('Temporary')

    class Meta:
        """Metaclass options for the Deployment model."""

        verbose_name = _('Deployment')
        verbose_name_plural = _('Deployments')
        constraints = [
            models.UniqueConstraint(
                fields=['device'],
                condition=Q(status=DeploymentStatus.DEPLOYED.value),
                name='fleet_deployment_one_active_per_device',
            ),
            models.UniqueConstraint(
                fields=['site'],
                condition=Q(status=DeploymentStatus.DEPLOYED.value),
                name='fleet_deployment_one_active_per_site',
            ),
        ]

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the Deployment model."""
        return reverse('api-fleet-deployment-list')

    @classmethod
    def api_defaults(cls, request=None):
        """Return default values for this model when issuing an API OPTIONS request."""
        return {'reference': generate_next_deployment_reference()}

    @classmethod
    def barcode_model_type_code(cls):
        """Return the associated barcode model type code for this model."""
        return 'DP'

    def save(self, *args, **kwargs):
        """Custom save method for the Deployment model."""
        self.reference_int = self.validate_reference_field(self.reference)
        super().save(*args, **kwargs)

    def clean(self):
        """Validate the deployment.

        - A deployment cannot replace itself
        - Only a replacement deployment can replace another one
        """
        super().clean()

        if self.replaces_id is not None:
            if self.pk is not None and self.replaces_id == self.pk:
                raise ValidationError({
                    'replaces': _('A deployment cannot replace itself')
                })

            if self.deployment_type != self.DeploymentType.REPLACEMENT:
                raise ValidationError({
                    'replaces': _(
                        'Only a replacement deployment can replace another one'
                    )
                })

    # State transitions (the work is done in fleet.services)

    @property
    def can_deploy(self) -> bool:
        """Return True if the device of this deployment can be deployed."""
        return self.status in DeploymentStatusGroups.DEPLOYABLE

    @property
    def can_schedule(self) -> bool:
        """Return True if this deployment can be scheduled on a field trip."""
        return self.status in DeploymentStatusGroups.DEPLOYABLE

    @property
    def can_recover(self) -> bool:
        """Return True if the device of this deployment can be recovered."""
        return self.status == DeploymentStatus.DEPLOYED.value

    def deploy(self, user=None, **kwargs):
        """Deploy the device (see fleet.services.deployment.deploy)."""
        return self.handle_transition(
            self.status,
            DeploymentStatus.DEPLOYED.value,
            self,
            self._action_deploy,
            user=user,
            **kwargs,
        )

    def _action_deploy(self, *args, **kwargs):
        """Action to be taken when the device is deployed."""
        from fleet.services import deployment

        return deployment.deploy(self, **kwargs)

    def recover(self, user=None, **kwargs):
        """Recover the device into stock (see fleet.services.deployment.recover)."""
        return self.handle_transition(
            self.status,
            DeploymentStatus.RECOVERED.value,
            self,
            self._action_recover,
            user=user,
            **kwargs,
        )

    def _action_recover(self, *args, **kwargs):
        """Action to be taken when the device is recovered."""
        from fleet.services import deployment

        return deployment.recover(self, **kwargs)

    def schedule(self, trip):
        """Schedule the deployment on a field trip (see fleet.services.deployment.schedule).

        Returns:
            The deployment (or swap) task
        """
        return self.handle_transition(
            self.status,
            DeploymentStatus.SCHEDULED.value,
            self,
            self._action_schedule,
            trip=trip,
        )

    def _action_schedule(self, *args, **kwargs):
        """Action to be taken when the deployment is scheduled on a trip."""
        from fleet.services import deployment

        return deployment.schedule(self, kwargs['trip'])

    def __str__(self):
        """String representation of a Deployment."""
        return self.reference

    def get_absolute_url(self):
        """Return the web URL associated with this Deployment."""
        return InvenTree.helpers.pui_url(f'/fleet/deployment/{self.pk}')

    def report_context(self) -> DeploymentReportContext:
        """Generate custom report context data."""
        return {
            'deployment': self,
            'reference': self.reference,
            'title': str(self),
            'site': self.site,
            'device': self.device,
            'streams': self.streams.all(),
            'alerts': self.alerts.all(),
            'tasks': self.tasks.all(),
        }

    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Deployment Reference'),
        default=generate_next_deployment_reference,
        validators=[validate_deployment_reference],
    )

    creation_date = models.DateField(
        auto_now_add=True, editable=False, verbose_name=_('Creation Date')
    )

    status = generic.states.fields.InvenTreeCustomStatusModelField(
        default=DeploymentStatus.PLANNED.value,
        choices=DeploymentStatus.items(),
        status_class=DeploymentStatus,
        validators=[MinValueValidator(0)],
        verbose_name=_('Status'),
        help_text=_('Deployment status'),
    )

    device_type = models.ForeignKey(
        FleetDeviceType,
        on_delete=models.PROTECT,
        related_name='deployments',
        verbose_name=_('Device Type'),
    )

    build = models.OneToOneField(
        'build.Build',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_deployment',
        verbose_name=_('Build Order'),
        help_text=_('Build order which produces the device'),
    )

    device = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_deployments',
        verbose_name=_('Device'),
        help_text=_('Serialized device'),
    )

    site = models.ForeignKey(
        Site,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='deployments',
        verbose_name=_('Site'),
    )

    deployment_type = models.CharField(
        max_length=20,
        choices=DeploymentType.choices,
        default=DeploymentType.NEW_STATION,
        verbose_name=_('Deployment Type'),
    )

    replaces = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='replaced_by',
        verbose_name=_('Replaces'),
        help_text=_('Deployment which this replacement ends'),
    )

    client = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_deployments',
        limit_choices_to={'is_customer': True},
        verbose_name=_('Client'),
    )

    coverage = models.CharField(
        max_length=20,
        choices=Coverage.choices,
        default=Coverage.FULL,
        verbose_name=_('Coverage'),
    )

    target_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_('Target Date'),
        help_text=_('Planned deployment date'),
    )

    deployed_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Deployed At')
    )

    recovered_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Recovered At')
    )

    latitude = latitude_field(_('Latitude'), blank=True, null=True)

    longitude = longitude_field(_('Longitude'), blank=True, null=True)

    depth_m = depth_field()

    geofence_radius_m = geofence_radius_field()

    geofence_polygon = geofence_polygon_field()

    next_pm_date = models.DateField(
        blank=True, null=True, verbose_name=_('Next PM Date')
    )

    next_pm_manual = models.BooleanField(
        default=False,
        verbose_name=_('Manual PM Date'),
        help_text=_('Next PM date was set by hand, and is not recalculated'),
    )

    # Live state cache (written by the monitoring service only)

    health = models.PositiveIntegerField(
        choices=HealthStatus.items(),
        default=HealthStatus.UNKNOWN.value,
        verbose_name=_('Health'),
    )

    last_contact = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Last Contact')
    )

    last_latitude = latitude_field(_('Last Latitude'), blank=True, null=True)

    last_longitude = longitude_field(_('Last Longitude'), blank=True, null=True)

    last_position_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Last Position At')
    )

    distance_from_nominal_m = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name=_('Distance From Nominal'),
        help_text=_('Distance from the nominal position (m)'),
    )

    last_polled_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Last Polled At')
    )


class DataStream(InvenTree.models.InvenTreeModel):
    """A data stream of a deployment, copied from the device type on deploy.

    Attributes:
        deployment: The deployment which reports this stream
        key: Stream identifier on the data platform
        name: Display name
        essential: Whether the device is unhealthy without this stream
        expected_interval_minutes: How often data is expected
        grace_minutes: Extra time allowed before the stream is late
        last_seen: Last time data was received (cached)
        state: Stream state (cached)
        enabled: Disabled streams are not monitored
    """

    class Meta:
        """Metaclass options for the DataStream model."""

        verbose_name = _('Data Stream')
        verbose_name_plural = _('Data Streams')
        unique_together = [('deployment', 'key')]

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-stream-list')

    def __str__(self):
        """String representation of a DataStream."""
        return f'{self.deployment} - {self.key}'

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name='streams',
        verbose_name=_('Deployment'),
    )

    key = models.CharField(max_length=100, verbose_name=_('Key'))

    name = models.CharField(max_length=100, blank=True, verbose_name=_('Name'))

    essential = models.BooleanField(default=False, verbose_name=_('Essential'))

    expected_interval_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1)],
        verbose_name=_('Expected Interval'),
    )

    grace_minutes = models.PositiveIntegerField(
        default=15, verbose_name=_('Grace Period')
    )

    last_seen = models.DateTimeField(blank=True, null=True, verbose_name=_('Last Seen'))

    state = models.PositiveIntegerField(
        choices=DataStreamStatus.items(),
        default=DataStreamStatus.UNKNOWN.value,
        verbose_name=_('State'),
    )

    enabled = models.BooleanField(default=True, verbose_name=_('Enabled'))


class PositionFix(InvenTree.models.InvenTreeModel):
    """A (downsampled) reported position of a deployed device.

    Attributes:
        deployment: The deployment which reported this position
        timestamp: Time of the position
        latitude: Reported latitude
        longitude: Reported longitude
        source: Source of the position (e.g. "platform")
    """

    class Meta:
        """Metaclass options for the PositionFix model."""

        verbose_name = _('Position Fix')
        verbose_name_plural = _('Position Fixes')
        ordering = ['deployment', 'timestamp']
        indexes = [models.Index(fields=['deployment', 'timestamp'])]

    def __str__(self):
        """String representation of a PositionFix."""
        return f'{self.deployment} @ {self.timestamp}'

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name='positions',
        verbose_name=_('Deployment'),
    )

    timestamp = models.DateTimeField(verbose_name=_('Timestamp'))

    latitude = latitude_field(_('Latitude'))

    longitude = longitude_field(_('Longitude'))

    source = models.CharField(
        max_length=50, blank=True, default='platform', verbose_name=_('Source')
    )


class Alert(
    InvenTree.models.ReferenceIndexingMixin,
    StateTransitionMixin,
    StatusCodeMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """An alert raised against a deployment or site.

    At most one unresolved alert may exist for each dedupe key, so that
    repeated polls update the existing alert instead of creating new ones.

    Attributes:
        reference: Unique reference (e.g. AL-00001)
        status: Alert status
        deployment: The deployment which raised the alert
        site: The site which raised the alert
        stream: The data stream which raised the alert
        alert_type: Type of alert
        severity: Alert severity
        message: Alert message
        data: Extra data about the alert
        opened_at: When the alert was opened
        acknowledged_at: When the alert was acknowledged
        acknowledged_by: Who acknowledged the alert
        resolved_at: When the alert was resolved
        resolved_by: Who resolved the alert
        resolution: How the alert was resolved
        task: Maintenance task which resolved the alert, or was created from it
        dedupe_key: Key which identifies the condition (e.g. "dp:12:STREAM_MISSING:hydrophone")
    """

    REFERENCE_PATTERN_SETTING = 'FLEET_ALERT_REFERENCE_PATTERN'
    STATUS_CLASS = AlertStatus

    class AlertType(models.TextChoices):
        """Type of alert."""

        STREAM_LATE = 'STREAM_LATE', _('Stream late')
        STREAM_MISSING = 'STREAM_MISSING', _('Stream missing')
        NO_CONTACT = 'NO_CONTACT', _('No contact')
        GEOFENCE_BREACH = 'GEOFENCE_BREACH', _('Geofence breach')
        PM_DUE = 'PM_DUE', _('Maintenance due')
        PM_OVERDUE = 'PM_OVERDUE', _('Maintenance overdue')
        NOT_READY = 'NOT_READY', _('Not ready')
        BUILD_LATE = 'BUILD_LATE', _('Build late')
        NO_DEPLOY_DATE = 'NO_DEPLOY_DATE', _('No deployment date')
        PARTS_SHORT = 'PARTS_SHORT', _('Parts short')

    class Resolution(models.TextChoices):
        """How an alert was resolved."""

        AUTO = 'AUTO', _('Automatically')
        TASK = 'TASK', _('By maintenance task')
        MANUAL = 'MANUAL', _('Manually')

    class Meta:
        """Metaclass options for the Alert model."""

        verbose_name = _('Alert')
        verbose_name_plural = _('Alerts')
        constraints = [
            models.UniqueConstraint(
                fields=['dedupe_key'],
                condition=~Q(status=AlertStatus.RESOLVED.value) & ~Q(dedupe_key=''),
                name='fleet_alert_one_unresolved_per_dedupe_key',
            )
        ]

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the Alert model."""
        return reverse('api-fleet-alert-list')

    @classmethod
    def api_defaults(cls, request=None):
        """Return default values for this model when issuing an API OPTIONS request."""
        return {'reference': generate_next_alert_reference()}

    def save(self, *args, **kwargs):
        """Custom save method for the Alert model."""
        self.reference_int = self.validate_reference_field(self.reference)
        super().save(*args, **kwargs)

    def __str__(self):
        """String representation of an Alert."""
        return f'{self.reference} - {self.get_alert_type_display()}'

    # State transitions (the work is done in fleet.services.monitoring)

    @property
    def can_acknowledge(self) -> bool:
        """Return True if this alert can be acknowledged."""
        return self.status == AlertStatus.OPEN.value

    @property
    def can_resolve(self) -> bool:
        """Return True if this alert can be resolved."""
        return self.status in AlertStatusGroups.OPEN

    def acknowledge(self, user=None):
        """Acknowledge the alert (see fleet.services.monitoring.acknowledge_alert)."""
        return self.handle_transition(
            self.status,
            AlertStatus.ACKNOWLEDGED.value,
            self,
            self._action_acknowledge,
            user=user,
        )

    def _action_acknowledge(self, *args, **kwargs):
        """Action to be taken when the alert is acknowledged."""
        from fleet.services import monitoring

        return monitoring.acknowledge_alert(self, user=kwargs.get('user'))

    def resolve(self, user=None, note: str = '', resolution=None):
        """Resolve the alert (by hand, unless another resolution is given)."""
        return self.handle_transition(
            self.status,
            AlertStatus.RESOLVED.value,
            self,
            self._action_resolve,
            user=user,
            note=note,
            resolution=resolution or self.Resolution.MANUAL,
        )

    def _action_resolve(self, *args, **kwargs):
        """Action to be taken when the alert is resolved."""
        from fleet.services import monitoring

        return monitoring.resolve_alert(self, **kwargs)

    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Alert Reference'),
        default=generate_next_alert_reference,
        validators=[validate_alert_reference],
    )

    status = generic.states.fields.InvenTreeCustomStatusModelField(
        default=AlertStatus.OPEN.value,
        choices=AlertStatus.items(),
        status_class=AlertStatus,
        validators=[MinValueValidator(0)],
        verbose_name=_('Status'),
        help_text=_('Alert status'),
    )

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='alerts',
        verbose_name=_('Deployment'),
    )

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='alerts',
        verbose_name=_('Site'),
    )

    stream = models.ForeignKey(
        DataStream,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='alerts',
        verbose_name=_('Stream'),
    )

    alert_type = models.CharField(
        max_length=20, choices=AlertType.choices, verbose_name=_('Alert Type')
    )

    severity = models.PositiveIntegerField(
        choices=AlertSeverity.items(),
        default=AlertSeverity.WARNING.value,
        verbose_name=_('Severity'),
    )

    message = models.CharField(max_length=500, blank=True, verbose_name=_('Message'))

    data = models.JSONField(blank=True, null=True, verbose_name=_('Data'))

    opened_at = models.DateTimeField(default=timezone.now, verbose_name=_('Opened At'))

    acknowledged_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Acknowledged At')
    )

    acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Acknowledged By'),
    )

    resolved_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Resolved At')
    )

    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Resolved By'),
    )

    resolution = models.CharField(
        max_length=20,
        choices=Resolution.choices,
        blank=True,
        verbose_name=_('Resolution'),
    )

    task = models.ForeignKey(
        'fleet.MaintenanceTask',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Task'),
        help_text=_(
            'Maintenance task which resolved this alert, or was created from it'
        ),
    )

    dedupe_key = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Dedupe Key'),
        help_text=_('Identifies the condition which raised the alert'),
    )


class FieldTripReportContext(report.mixins.BaseReportContext):
    """Context for the FieldTrip model.

    Attributes:
        trip: The FieldTrip object
        reference: The reference of the trip
        title: The title of the report
        tasks: Query set of MaintenanceTask objects
        kit_lines: Query set of TripKitLine objects
        actions: Query set of the MaintenanceAction objects of the trip tasks
        parts_used: Parts installed or consumed (part, name, quantity, serials)
        alerts_resolved: Query set of the Alert objects resolved by the trip tasks
        team: Query set of the User objects on the trip team
    """

    trip: 'FieldTrip'
    reference: str
    title: str
    tasks: report.mixins.QuerySet['MaintenanceTask']
    kit_lines: report.mixins.QuerySet['TripKitLine']
    actions: report.mixins.QuerySet['MaintenanceAction']
    parts_used: list[dict]
    alerts_resolved: report.mixins.QuerySet['Alert']
    team: report.mixins.QuerySet[User]


class FieldTrip(
    report.mixins.InvenTreeReportMixin,
    InvenTree.models.InvenTreeAttachmentMixin,
    InvenTree.models.InvenTreeBarcodeMixin,
    InvenTree.models.InvenTreeNotesMixin,
    InvenTree.models.ReferenceIndexingMixin,
    StateTransitionMixin,
    StatusCodeMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """A field trip, which carries out a group of maintenance tasks.

    Attributes:
        reference: Unique reference (e.g. TRIP-0001)
        status: Trip status
        title: Trip title
        start_date: Start date
        end_date: End date
        vessel: Vessel used for the trip
        team: Users on the trip
        responsible: User or group responsible for the trip
        kit_location: Stock location which holds the trip parts kit
    """

    REFERENCE_PATTERN_SETTING = 'FLEET_TRIP_REFERENCE_PATTERN'
    STATUS_CLASS = TripStatus

    class Meta:
        """Metaclass options for the FieldTrip model."""

        verbose_name = _('Field Trip')
        verbose_name_plural = _('Field Trips')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the FieldTrip model."""
        return reverse('api-fleet-trip-list')

    @classmethod
    def api_defaults(cls, request=None):
        """Return default values for this model when issuing an API OPTIONS request."""
        return {'reference': generate_next_trip_reference()}

    @classmethod
    def barcode_model_type_code(cls):
        """Return the associated barcode model type code for this model."""
        return 'FT'

    def save(self, *args, **kwargs):
        """Custom save method for the FieldTrip model."""
        self.reference_int = self.validate_reference_field(self.reference)
        super().save(*args, **kwargs)

    def clean(self):
        """Validate the trip: it cannot end before it starts."""
        super().clean()

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': _('The end date cannot be before the start date')
            })

    # State transitions (the work is done in fleet.services.trips)

    @property
    def can_prepare_kit(self) -> bool:
        """Return True if stock can be moved into the kit of this trip."""
        return self.status in TripStatusGroups.KIT

    @property
    def can_start(self) -> bool:
        """Return True if this trip can be started."""
        return self.status in TripStatusGroups.STARTABLE

    @property
    def can_reconcile(self) -> bool:
        """Return True if this trip can be reconciled."""
        return self.status in TripStatusGroups.RECONCILABLE

    @property
    def can_cancel(self) -> bool:
        """Return True if this trip can be cancelled."""
        return self.status in TripStatusGroups.CANCELLABLE

    def prepare_kit(self, items, user=None):
        """Move stock into the trip kit (see fleet.services.trips.prepare_kit)."""
        return self.handle_transition(
            self.status,
            TripStatus.KIT_READY.value,
            self,
            self._action_prepare_kit,
            items=items,
            user=user,
        )

    def _action_prepare_kit(self, *args, **kwargs):
        """Action to be taken when the kit is prepared."""
        from fleet.services import trips

        return trips.prepare_kit(self, kwargs['items'], user=kwargs.get('user'))

    def start_trip(self, user=None):
        """Start the trip (see fleet.services.trips.start_trip)."""
        return self.handle_transition(
            self.status,
            TripStatus.IN_PROGRESS.value,
            self,
            self._action_start,
            user=user,
        )

    def _action_start(self, *args, **kwargs):
        """Action to be taken when the trip is started."""
        from fleet.services import trips

        return trips.start_trip(self, user=kwargs.get('user'))

    def reconcile_trip(self, user=None, returns=None):
        """Return the kit and close the trip (see fleet.services.trips.reconcile).

        Returns:
            The reconcile result
        """
        return self.handle_transition(
            self.status,
            TripStatus.CLOSED.value,
            self,
            self._action_reconcile,
            user=user,
            returns=returns,
        )

    def _action_reconcile(self, *args, **kwargs):
        """Action to be taken when the trip is reconciled."""
        from fleet.services import trips

        return trips.reconcile(
            self, user=kwargs.get('user'), returns=kwargs.get('returns')
        )

    def cancel_trip(self, user=None, reason: str = ''):
        """Cancel the trip (see fleet.services.trips.cancel_trip)."""
        return self.handle_transition(
            self.status,
            TripStatus.CANCELLED.value,
            self,
            self._action_cancel,
            user=user,
            reason=reason,
        )

    def _action_cancel(self, *args, **kwargs):
        """Action to be taken when the trip is cancelled."""
        from fleet.services import trips

        return trips.cancel_trip(
            self, user=kwargs.get('user'), reason=kwargs.get('reason', '')
        )

    def __str__(self):
        """String representation of a FieldTrip."""
        return f'{self.reference} - {self.title}'

    def get_absolute_url(self):
        """Return the web URL associated with this FieldTrip."""
        return InvenTree.helpers.pui_url(f'/fleet/trip/{self.pk}')

    def report_context(self) -> FieldTripReportContext:
        """Generate custom report context data."""
        actions = MaintenanceAction.objects.filter(task__trip=self).select_related(
            'task', 'part', 'component_in', 'component_out', 'fault_code'
        )

        return {
            'trip': self,
            'reference': self.reference,
            'title': str(self),
            'tasks': self.tasks.select_related('device', 'site', 'deployment').order_by(
                'reference_int'
            ),
            'kit_lines': self.kit_lines.select_related('part', 'stock_item'),
            'actions': actions.order_by('task__reference_int', 'created_at', 'pk'),
            'parts_used': self.parts_used(),
            'alerts_resolved': Alert.objects.filter(
                task__trip=self, status=AlertStatus.RESOLVED.value
            ).order_by('reference_int'),
            'team': self.team.all(),
        }

    def parts_used(self) -> list[dict]:
        """Parts installed or consumed by the tasks of this trip.

        Returns:
            One dict per part: pk, name, quantity and the installed serials
        """
        used = {}

        for action in MaintenanceAction.objects.filter(
            task__trip=self,
            action__in=[
                MaintenanceAction.Action.REPLACE,
                MaintenanceAction.Action.ADD,
                MaintenanceAction.Action.CONSUME,
            ],
            part__isnull=False,
        ).select_related('part', 'component_in'):
            row = used.setdefault(
                action.part.pk,
                {
                    'part': action.part.pk,
                    'name': action.part.full_name,
                    'quantity': 0,
                    'serials': [],
                },
            )

            row['quantity'] += float(action.quantity or 0)

            if action.component_in is not None and action.component_in.serial:
                row['serials'].append(action.component_in.serial)

        return sorted(used.values(), key=lambda row: row['name'])

    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Field Trip Reference'),
        default=generate_next_trip_reference,
        validators=[validate_trip_reference],
    )

    status = generic.states.fields.InvenTreeCustomStatusModelField(
        default=TripStatus.PLANNING.value,
        choices=TripStatus.items(),
        status_class=TripStatus,
        validators=[MinValueValidator(0)],
        verbose_name=_('Status'),
        help_text=_('Trip status'),
    )

    title = models.CharField(max_length=100, verbose_name=_('Title'))

    start_date = models.DateField(verbose_name=_('Start Date'))

    end_date = models.DateField(blank=True, null=True, verbose_name=_('End Date'))

    vessel = models.CharField(max_length=100, blank=True, verbose_name=_('Vessel'))

    team = models.ManyToManyField(
        User, blank=True, related_name='fleet_trips', verbose_name=_('Team')
    )

    responsible = models.ForeignKey(
        users.models.Owner,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_trips_responsible',
        verbose_name=_('Responsible'),
        help_text=_('User or group responsible for this trip'),
    )

    kit_location = models.ForeignKey(
        'stock.StockLocation',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_trips',
        verbose_name=_('Kit Location'),
        help_text=_('Stock location which holds the parts kit for this trip'),
    )


class TripKitLine(InvenTree.models.InvenTreeModel):
    """A part planned to be taken on a field trip.

    Attributes:
        trip: The field trip
        part: The part to take
        quantity_planned: Quantity to take
        source: Why the part is in the kit
        note: Free text note
        stock_item: The device to take (a device to deploy)
        task: The task which needs the line (a device to deploy)
    """

    class Source(models.TextChoices):
        """Why a part is in the kit."""

        ALWAYS = 'ALWAYS', _('Always')
        LIKELY = 'LIKELY', _('Likely')
        ALERT = 'ALERT', _('Alert')
        MANUAL = 'MANUAL', _('Manual')
        DEVICE = 'DEVICE', _('Device to deploy')

    class Meta:
        """Metaclass options for the TripKitLine model."""

        verbose_name = _('Trip Kit Line')
        verbose_name_plural = _('Trip Kit Lines')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the TripKitLine model."""
        return reverse('api-fleet-trip-kit-line-list')

    def __str__(self):
        """String representation of a TripKitLine."""
        return f'{self.trip} - {self.part}'

    trip = models.ForeignKey(
        FieldTrip,
        on_delete=models.CASCADE,
        related_name='kit_lines',
        verbose_name=_('Field Trip'),
    )

    part = models.ForeignKey(
        'part.Part',
        on_delete=models.CASCADE,
        related_name='fleet_trip_kit_lines',
        verbose_name=_('Part'),
    )

    quantity_planned = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        default=1,
        validators=[MinValueValidator(0)],
        verbose_name=_('Planned Quantity'),
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name=_('Source'),
    )

    note = models.CharField(max_length=250, blank=True, verbose_name=_('Note'))

    stock_item = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='fleet_trip_kit_lines',
        verbose_name=_('Stock Item'),
        help_text=_('The device to take (a device to deploy)'),
    )

    task = models.ForeignKey(
        'fleet.MaintenanceTask',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='kit_lines',
        verbose_name=_('Task'),
        help_text=_('The task which needs this line'),
    )


class MaintenanceTaskReportContext(report.mixins.BaseReportContext):
    """Context for the MaintenanceTask model.

    Attributes:
        task: The MaintenanceTask object
        reference: The reference of the task
        title: The title of the report
        device: The serialized device which was worked on
        deployment: The deployment context of the task
        site: The site of the task (or of its deployment)
        display_name: The site nickname, else the device serial
        checklist: Query set of ChecklistResult objects
        actions: Query set of MaintenanceAction objects
        alerts: Query set of Alert objects addressed by the task
        technicians: Query set of the User objects who worked on the task
    """

    task: 'MaintenanceTask'
    reference: str
    title: str
    device: 'stock.models.StockItem'
    deployment: 'Deployment | None'
    site: 'Site | None'
    display_name: str
    checklist: report.mixins.QuerySet['ChecklistResult']
    actions: report.mixins.QuerySet['MaintenanceAction']
    alerts: report.mixins.QuerySet['Alert']
    technicians: report.mixins.QuerySet[User]


class MaintenanceTask(
    report.mixins.InvenTreeReportMixin,
    InvenTree.models.InvenTreeAttachmentMixin,
    InvenTree.models.InvenTreeBarcodeMixin,
    InvenTree.models.InvenTreeNotesMixin,
    InvenTree.models.ReferenceIndexingMixin,
    StateTransitionMixin,
    StatusCodeMixin,
    InvenTree.models.MetadataMixin,
    InvenTree.models.InvenTreeModel,
):
    """A unit of maintenance work on a serialized device.

    Attributes:
        reference: Unique reference (e.g. MT-0001)
        status: Task status
        task_type: Type of task
        description: What needs to be done
        device: The serialized device worked on
        deployment: Deployment context (null for a workshop task)
        site: Site of the task
        trip: Field trip which carries out the task
        due_date: When the task is due
        scheduled_date: When the task is scheduled
        started_at: When work started
        started_by: Who started the work
        completed_at: When work was completed
        completed_by: Who completed the work
        technicians: Users who worked on the task
        labour_minutes: Time spent (minutes)
        as_found: Condition of the device as found
        summary: Summary of the work done
        verification: Result of the data verification step
        verification_override_reason: Why the task was closed without passing verification
        follow_up_of: The task which this task follows up
        alerts: Alerts which this task addresses
    """

    REFERENCE_PATTERN_SETTING = 'FLEET_TASK_REFERENCE_PATTERN'
    STATUS_CLASS = TaskStatus

    TaskType = TaskType

    class Meta:
        """Metaclass options for the MaintenanceTask model."""

        verbose_name = _('Maintenance Task')
        verbose_name_plural = _('Maintenance Tasks')

    @staticmethod
    def get_api_url():
        """Return the API URL associated with the MaintenanceTask model."""
        return reverse('api-fleet-task-list')

    @classmethod
    def api_defaults(cls, request=None):
        """Return default values for this model when issuing an API OPTIONS request."""
        return {'reference': generate_next_task_reference()}

    @classmethod
    def barcode_model_type_code(cls):
        """Return the associated barcode model type code for this model."""
        return 'MT'

    def save(self, *args, **kwargs):
        """Custom save method for the MaintenanceTask model."""
        self.reference_int = self.validate_reference_field(self.reference)
        super().save(*args, **kwargs)

    @staticmethod
    def validate_device(device, deployment) -> None:
        """A task needs a single serialized unit, matching its deployment."""
        if not device.serialized or device.quantity != 1:
            raise ValidationError({'device': _('Select a single serialized unit')})

        if deployment is not None and deployment.device_id != device.pk:
            raise ValidationError({
                'deployment': _('The deployment is for a different device')
            })

    def clean(self):
        """Validate the task (see validate_device)."""
        super().clean()

        if self.device_id is not None:
            self.validate_device(
                self.device, self.deployment if self.deployment_id else None
            )

    # State transitions (the work is done in fleet.services.maintenance)

    @property
    def can_start(self) -> bool:
        """Return True if this task can be started."""
        return self.status in TaskStatusGroups.STARTABLE

    @property
    def can_complete(self) -> bool:
        """Return True if this task can be completed."""
        return self.status == TaskStatus.IN_PROGRESS.value

    @property
    def can_cancel(self) -> bool:
        """Return True if this task can be cancelled."""
        return self.status in TaskStatusGroups.OPEN

    def start_task(self, user=None):
        """Start the task (see fleet.services.maintenance.start)."""
        return self.handle_transition(
            self.status,
            TaskStatus.IN_PROGRESS.value,
            self,
            self._action_start,
            user=user,
        )

    def _action_start(self, *args, **kwargs):
        """Action to be taken when the task is started."""
        from fleet.services import maintenance

        return maintenance.start(self, user=kwargs.get('user'))

    def complete_task(self, user=None, **kwargs):
        """Close the task (see fleet.services.maintenance.complete)."""
        return self.handle_transition(
            self.status,
            TaskStatus.COMPLETED.value,
            self,
            self._action_complete,
            user=user,
            **kwargs,
        )

    def _action_complete(self, *args, **kwargs):
        """Action to be taken when the task is completed."""
        from fleet.services import maintenance

        return maintenance.complete(self, **kwargs)

    def cancel_task(self, user=None, reason: str = ''):
        """Cancel the task (see fleet.services.maintenance.cancel)."""
        return self.handle_transition(
            self.status,
            TaskStatus.CANCELLED.value,
            self,
            self._action_cancel,
            user=user,
            reason=reason,
        )

    def _action_cancel(self, *args, **kwargs):
        """Action to be taken when the task is cancelled."""
        from fleet.services import maintenance

        return maintenance.cancel(
            self, user=kwargs.get('user'), reason=kwargs.get('reason', '')
        )

    def __str__(self):
        """String representation of a MaintenanceTask."""
        return self.reference

    def get_absolute_url(self):
        """Return the web URL associated with this MaintenanceTask."""
        return InvenTree.helpers.pui_url(f'/fleet/task/{self.pk}')

    def report_context(self) -> MaintenanceTaskReportContext:
        """Generate custom report context data."""
        site = self.site or (self.deployment.site if self.deployment else None)

        return {
            'task': self,
            'reference': self.reference,
            'title': str(self),
            'device': self.device,
            'deployment': self.deployment,
            'site': site,
            'display_name': site.name if site else (self.device.serial or ''),
            'checklist': self.checklist.all(),
            'actions': self.actions.all(),
            'alerts': self.alerts.all(),
            'technicians': self.technicians.all(),
        }

    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Maintenance Task Reference'),
        default=generate_next_task_reference,
        validators=[validate_task_reference],
    )

    status = generic.states.fields.InvenTreeCustomStatusModelField(
        default=TaskStatus.PROPOSED.value,
        choices=TaskStatus.items(),
        status_class=TaskStatus,
        validators=[MinValueValidator(0)],
        verbose_name=_('Status'),
        help_text=_('Task status'),
    )

    task_type = models.CharField(
        max_length=20,
        choices=TaskType.choices,
        default=TaskType.PREVENTIVE,
        verbose_name=_('Task Type'),
    )

    description = models.CharField(
        max_length=250,
        blank=True,
        verbose_name=_('Description'),
        help_text=_('What needs to be done'),
    )

    device = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.PROTECT,
        related_name='fleet_tasks',
        verbose_name=_('Device'),
        help_text=_('Serialized device worked on'),
    )

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
        verbose_name=_('Deployment'),
    )

    site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
        verbose_name=_('Site'),
    )

    trip = models.ForeignKey(
        FieldTrip,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
        verbose_name=_('Field Trip'),
    )

    due_date = models.DateField(blank=True, null=True, verbose_name=_('Due Date'))

    scheduled_date = models.DateField(
        blank=True, null=True, verbose_name=_('Scheduled Date')
    )

    started_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Started At')
    )

    started_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Started By'),
    )

    completed_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Completed At')
    )

    completed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Completed By'),
    )

    technicians = models.ManyToManyField(
        User, blank=True, related_name='fleet_tasks', verbose_name=_('Technicians')
    )

    labour_minutes = models.PositiveIntegerField(
        blank=True, null=True, verbose_name=_('Labour'), help_text=_('Minutes')
    )

    as_found = models.TextField(blank=True, verbose_name=_('As Found'))

    summary = models.TextField(blank=True, verbose_name=_('Summary'))

    verification = models.JSONField(
        blank=True, null=True, verbose_name=_('Verification')
    )

    verification_override_reason = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('Verification Override Reason'),
        help_text=_('Why the task was closed without passing verification'),
    )

    follow_up_of = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='follow_ups',
        verbose_name=_('Follow-up Of'),
    )

    alerts = models.ManyToManyField(
        Alert, blank=True, related_name='tasks', verbose_name=_('Alerts')
    )


class ChecklistResult(InvenTree.models.InvenTreeModel):
    """The result of one checklist item, for a maintenance task.

    Attributes:
        task: The maintenance task
        template_item: The checklist template item this result was created from
        sequence: Display order
        text: The check performed
        result: Result of the check
        value: Measured value
        note: Free text note
        fault_code: Fault found
    """

    class Result(models.TextChoices):
        """Result of a checklist item."""

        PENDING = 'PENDING', _('Pending')
        OK = 'OK', _('OK')
        ISSUE = 'ISSUE', _('Issue')
        NA = 'NA', _('N/A')

    class Meta:
        """Metaclass options for the ChecklistResult model."""

        verbose_name = _('Checklist Result')
        verbose_name_plural = _('Checklist Results')
        ordering = ['task', 'sequence', 'pk']

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-checklist-result-list')

    def __str__(self):
        """String representation of a ChecklistResult."""
        return f'{self.task} - {self.text}'

    task = models.ForeignKey(
        MaintenanceTask,
        on_delete=models.CASCADE,
        related_name='checklist',
        verbose_name=_('Task'),
    )

    template_item = models.ForeignKey(
        ChecklistTemplateItem,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='results',
        verbose_name=_('Template Item'),
    )

    sequence = models.PositiveIntegerField(default=0, verbose_name=_('Sequence'))

    text = models.CharField(max_length=250, verbose_name=_('Text'))

    required = models.BooleanField(default=True, verbose_name=_('Required'))

    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        default=Result.PENDING,
        verbose_name=_('Result'),
    )

    value = models.CharField(max_length=100, blank=True, verbose_name=_('Value'))

    note = models.CharField(max_length=500, blank=True, verbose_name=_('Note'))

    fault_code = models.ForeignKey(
        FaultCode,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='checklist_results',
        verbose_name=_('Fault Code'),
    )


class MaintenanceAction(
    InvenTree.models.MetadataMixin, InvenTree.models.InvenTreeModel
):
    """A physical or logical action performed during a maintenance task.

    Attributes:
        task: The maintenance task
        action: Type of action
        component_out: The component which was removed, destroyed or replaced
        component_in: The component which was installed
        part: The part used (e.g. for a consumable)
        quantity: Quantity used
        disposition: What happened to the removed component
        destination: Where the removed component was placed
        fault_code: Fault which the action addresses
        checklist_result: Checklist result which the action addresses
        note: Free text note
        created_at: When the action was recorded
        created_by: Who recorded the action
    """

    class Action(models.TextChoices):
        """Type of maintenance action."""

        REPLACE = 'REPLACE', _('Replace')
        REMOVE = 'REMOVE', _('Remove')
        ADD = 'ADD', _('Add')
        DESTROY = 'DESTROY', _('Destroy')
        CONSUME = 'CONSUME', _('Consume')
        REPAIR = 'REPAIR', _('Repair')
        CLEAN = 'CLEAN', _('Clean')
        FIRMWARE = 'FIRMWARE', _('Firmware')
        REPOSITION = 'REPOSITION', _('Reposition')
        OTHER = 'OTHER', _('Other')

    class Meta:
        """Metaclass options for the MaintenanceAction model."""

        verbose_name = _('Maintenance Action')
        verbose_name_plural = _('Maintenance Actions')
        ordering = ['task', 'created_at', 'pk']

    @staticmethod
    def get_api_url():
        """Return the API URL associated with this model."""
        return reverse('api-fleet-task-action-list')

    def __str__(self):
        """String representation of a MaintenanceAction."""
        return f'{self.task} - {self.get_action_display()}'

    task = models.ForeignKey(
        MaintenanceTask,
        on_delete=models.CASCADE,
        related_name='actions',
        verbose_name=_('Task'),
    )

    action = models.CharField(
        max_length=20, choices=Action.choices, verbose_name=_('Action')
    )

    component_out = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Component Out'),
    )

    component_in = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Component In'),
    )

    part = models.ForeignKey(
        'part.Part',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Part'),
    )

    quantity = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name=_('Quantity'),
    )

    disposition = models.CharField(
        max_length=20, blank=True, verbose_name=_('Disposition')
    )

    destination = models.ForeignKey(
        'stock.StockLocation',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Destination'),
    )

    fault_code = models.ForeignKey(
        FaultCode,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='actions',
        verbose_name=_('Fault Code'),
    )

    checklist_result = models.ForeignKey(
        ChecklistResult,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='actions',
        verbose_name=_('Checklist Result'),
    )

    note = models.CharField(max_length=500, blank=True, verbose_name=_('Note'))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='+',
        verbose_name=_('Created By'),
    )
