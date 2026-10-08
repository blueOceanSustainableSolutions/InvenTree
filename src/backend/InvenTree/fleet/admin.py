"""Admin functionality for the Fleet app."""

from django.contrib import admin

from fleet import models


class StreamTemplateInline(admin.TabularInline):
    """Inline for the stream templates of a device type."""

    model = models.StreamTemplate
    extra = 0


class ChecklistTemplateItemInline(admin.TabularInline):
    """Inline for the checklist template of a device type."""

    model = models.ChecklistTemplateItem
    extra = 0


class KitTemplateLineInline(admin.TabularInline):
    """Inline for the kit template of a device type."""

    model = models.KitTemplateLine
    extra = 0
    autocomplete_fields = ['part']


@admin.register(models.FleetDeviceType)
class FleetDeviceTypeAdmin(admin.ModelAdmin):
    """Admin class for the FleetDeviceType model."""

    list_display = ('part', 'pm_interval_days', 'verify_window_minutes', 'active')
    list_filter = ('active',)
    search_fields = ('part__name', 'part__IPN', 'part__description')
    autocomplete_fields = ['part']
    inlines = [StreamTemplateInline, ChecklistTemplateItemInline, KitTemplateLineInline]


@admin.register(models.FaultCode)
class FaultCodeAdmin(admin.ModelAdmin):
    """Admin class for the FaultCode model."""

    list_display = ('code', 'name', 'category', 'active')
    list_filter = ('category', 'active')
    search_fields = ('code', 'name')


@admin.register(models.Site)
class SiteAdmin(admin.ModelAdmin):
    """Admin class for the Site model."""

    list_display = ('reference', 'name', 'coverage', 'client', 'project', 'active')
    list_filter = ('coverage', 'active')
    search_fields = ('reference', 'name', 'project')
    autocomplete_fields = ['client']


@admin.register(models.DeviceLink)
class DeviceLinkAdmin(admin.ModelAdmin):
    """Admin class for the DeviceLink model."""

    list_display = ('stock_item', 'platform_id', 'firmware_version')
    search_fields = ('platform_id', 'stock_item__serial', 'stock_item__part__name')
    autocomplete_fields = ['stock_item']


class DataStreamInline(admin.TabularInline):
    """Inline for the data streams of a deployment."""

    model = models.DataStream
    extra = 0


@admin.register(models.Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    """Admin class for the Deployment model."""

    list_display = (
        'reference',
        'status',
        'device_type',
        'device',
        'site',
        'target_date',
        'deployed_at',
        'health',
    )
    list_filter = ('status', 'deployment_type', 'coverage', 'health')
    search_fields = ('reference', 'site__name', 'site__reference', 'device__serial')
    autocomplete_fields = [
        'device_type',
        'build',
        'device',
        'site',
        'replaces',
        'client',
    ]
    inlines = [DataStreamInline]


@admin.register(models.PositionFix)
class PositionFixAdmin(admin.ModelAdmin):
    """Admin class for the PositionFix model."""

    list_display = ('deployment', 'timestamp', 'latitude', 'longitude', 'source')
    autocomplete_fields = ['deployment']


@admin.register(models.Alert)
class AlertAdmin(admin.ModelAdmin):
    """Admin class for the Alert model."""

    list_display = (
        'reference',
        'status',
        'alert_type',
        'severity',
        'deployment',
        'site',
        'opened_at',
    )
    list_filter = ('status', 'alert_type', 'severity', 'resolution')
    search_fields = ('reference', 'message', 'dedupe_key')
    autocomplete_fields = [
        'deployment',
        'site',
        'task',
        'acknowledged_by',
        'resolved_by',
    ]
    raw_id_fields = ['stream']


class TripKitLineInline(admin.TabularInline):
    """Inline for the kit lines of a field trip."""

    model = models.TripKitLine
    extra = 0
    autocomplete_fields = ['part']


@admin.register(models.FieldTrip)
class FieldTripAdmin(admin.ModelAdmin):
    """Admin class for the FieldTrip model."""

    list_display = ('reference', 'title', 'status', 'start_date', 'end_date', 'vessel')
    list_filter = ('status',)
    search_fields = ('reference', 'title', 'vessel')
    autocomplete_fields = ['team', 'responsible', 'kit_location']
    inlines = [TripKitLineInline]


class ChecklistResultInline(admin.TabularInline):
    """Inline for the checklist results of a maintenance task."""

    model = models.ChecklistResult
    extra = 0
    raw_id_fields = ['template_item']


class MaintenanceActionInline(admin.TabularInline):
    """Inline for the actions of a maintenance task."""

    model = models.MaintenanceAction
    extra = 0
    raw_id_fields = [
        'component_out',
        'component_in',
        'part',
        'destination',
        'checklist_result',
    ]


@admin.register(models.MaintenanceTask)
class MaintenanceTaskAdmin(admin.ModelAdmin):
    """Admin class for the MaintenanceTask model."""

    list_display = (
        'reference',
        'task_type',
        'status',
        'device',
        'site',
        'trip',
        'due_date',
        'scheduled_date',
    )
    list_filter = ('status', 'task_type')
    search_fields = ('reference', 'device__serial', 'site__name', 'summary')
    autocomplete_fields = [
        'device',
        'deployment',
        'site',
        'trip',
        'started_by',
        'completed_by',
        'technicians',
        'follow_up_of',
        'alerts',
    ]
    inlines = [ChecklistResultInline, MaintenanceActionInline]
