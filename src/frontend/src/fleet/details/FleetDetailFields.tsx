import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';

import { ModelType } from '@lib/enums/ModelType';
import type { DetailsField } from '../../components/details/Details';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { formatDate } from '../../defaults/formatters';
import {
  CoverageBadge,
  FleetStatus,
  FleetStatusBadge,
  taskTypeLabel,
  userDisplayName,
  userListNames
} from '../components/FleetBadges';
import { CoordinateValue, PositionValue } from '../coordinates';

/**
 * Detail field definitions of the fleet models, shared by the detail pages
 * of the main UI (rendered with DetailsTable) and the Fleet Portal (rendered
 * with PortalDetailFields, which picks the fields it shows by name).
 */
export type FleetDetailFields = {
  left: DetailsField[];
  right: DetailsField[];
};

/** All the fields of a set, by name (for the portal, which picks some) */
export function detailFieldsByName(
  fields: FleetDetailFields
): Record<string, DetailsField> {
  const result: Record<string, DetailsField> = {};

  [...fields.left, ...fields.right].forEach((field) => {
    result[field.name] = field;
  });

  return result;
}

/** The "Custom Status" row, shown when a custom status is set (as core) */
function customStatusField(item: any, model: ModelType): DetailsField {
  return {
    type: 'status',
    name: 'status_custom_key',
    label: t`Custom Status`,
    model: model,
    icon: 'status',
    hidden: !item.status_custom_key || item.status_custom_key == item.status
  };
}

/**
 * Deployment fields: the deployment itself (left), dates and position (right)
 */
export function deploymentDetailFields(
  deployment: any,
  {
    isDeployed,
    hasPosition
  }: {
    isDeployed: boolean;
    hasPosition: boolean;
  }
): FleetDetailFields {
  const left: DetailsField[] = [
    {
      type: 'string',
      name: 'reference',
      label: t`Reference`,
      icon: 'reference',
      copy: true
    },
    {
      type: 'status',
      name: 'status',
      label: t`Status`,
      model: ModelType.deployment
    },
    customStatusField(deployment, ModelType.deployment),
    {
      type: 'string',
      name: 'deployment_type',
      label: t`Deployment Type`,
      icon: 'info'
    },
    {
      type: 'link',
      name: 'device_type',
      label: t`Device Type`,
      icon: 'fleet_device_type',
      model: ModelType.fleetdevicetype,
      model_filters: { part_detail: true },
      model_formatter: (deviceType: any) =>
        deviceType?.part_detail?.full_name ??
        deviceType?.part_name ??
        deployment.device_type_detail?.part_name,
      backup_value: deployment.device_type_detail?.part_name
    },
    // The device: the unit when it is built, else the build order which
    // makes it (its material is on the Device Materials panel)
    {
      type: 'link',
      name: 'device',
      label: t`Device`,
      icon: 'serial',
      model: ModelType.stockitem,
      model_formatter: (device: any) =>
        device?.serial ?? deployment.device_detail?.serial,
      backup_value: deployment.device_detail?.serial,
      hidden: !deployment.device
    },
    {
      type: 'link',
      name: 'build_detail.pk',
      label: t`Device`,
      icon: 'serial',
      model: ModelType.build,
      model_formatter: (build: any) =>
        [build?.reference, build?.title, t`in production`]
          .filter(Boolean)
          .join(' · '),
      hidden: !!deployment.device || !deployment.build
    },
    {
      type: 'string',
      name: 'device_missing',
      label: t`Device`,
      icon: 'serial',
      hidden: !!deployment.device || !!deployment.build,
      value_formatter: () => (
        <Text size='sm' c='orange'>{t`Not built yet, no build order`}</Text>
      )
    },
    // Outside view of the build order (read only): is it done, and when is
    // it due. The fleet does not create or change build orders.
    {
      type: 'string',
      name: 'build_status',
      label: t`Build Status`,
      icon: 'status',
      hidden: !deployment.build_detail,
      value_formatter: () => (
        <StatusRenderer
          status={
            deployment.build_detail?.status_custom_key ||
            deployment.build_detail?.status
          }
          type={ModelType.build}
        />
      )
    },
    {
      type: 'string',
      name: 'build_target_date',
      label: t`Build Target Date`,
      icon: 'calendar',
      hidden: !deployment.build_detail,
      value_formatter: () =>
        deployment.build_detail?.target_date ? (
          formatDate(deployment.build_detail.target_date)
        ) : (
          <Text size='sm' c='dimmed'>{t`Not set`}</Text>
        )
    },
    {
      type: 'link',
      name: 'site',
      label: t`Site`,
      icon: 'fleet_site',
      model: ModelType.site,
      hidden: !deployment.site
    },
    {
      type: 'link',
      name: 'replaces',
      label: t`Replaces`,
      icon: 'fleet_deployment',
      model: ModelType.deployment,
      model_field: 'reference',
      hidden: !deployment.replaces
    },
    {
      type: 'link',
      name: 'client',
      label: t`Client`,
      icon: 'customer',
      model: ModelType.company,
      hidden: !deployment.client
    },
    {
      type: 'string',
      name: 'coverage',
      label: t`Coverage`,
      icon: 'info',
      value_formatter: () => <CoverageBadge coverage={deployment.coverage} />
    }
  ];

  const right: DetailsField[] = [
    {
      type: 'date',
      name: 'target_date',
      label: t`Target Date`,
      icon: 'calendar',
      hidden: !deployment.target_date
    },
    {
      type: 'date',
      name: 'creation_date',
      label: t`Created`,
      icon: 'calendar'
    },
    {
      type: 'date',
      name: 'deployed_at',
      label: t`Deployed`,
      icon: 'calendar',
      showTime: true,
      hidden: !deployment.deployed_at
    },
    {
      type: 'date',
      name: 'recovered_at',
      label: t`Recovered`,
      icon: 'calendar',
      showTime: true,
      hidden: !deployment.recovered_at
    },
    {
      // Where the device should be: the location of its site
      type: 'string',
      name: 'site_location',
      label: t`Site Location`,
      icon: 'location',
      hidden: !deployment.site_detail && !isDeployed,
      value_formatter: () =>
        hasPosition ? (
          <PositionValue
            latitude={deployment.site_detail.latitude}
            longitude={deployment.site_detail.longitude}
            suffix={
              deployment.site_detail.depth_m != null
                ? ` (${deployment.site_detail.depth_m} m)`
                : ''
            }
          />
        ) : (
          <Text size='sm' c={isDeployed ? 'orange' : 'dimmed'}>
            {deployment.site_detail ? t`Not set` : t`No site`}
          </Text>
        )
    },
    {
      // Where the device says it is (data platform)
      type: 'string',
      name: 'reported_position',
      label: t`Reported Position`,
      icon: 'location',
      hidden: deployment.last_latitude == null,
      value_formatter: () => (
        <PositionValue
          latitude={deployment.last_latitude}
          longitude={deployment.last_longitude}
          suffix={
            deployment.distance_from_site_m != null
              ? ` (${Math.round(deployment.distance_from_site_m)} m ${t`from the site`})`
              : ''
          }
        />
      )
    },
    {
      type: 'date',
      name: 'next_pm_date',
      label: t`Next PM`,
      icon: 'calendar',
      hidden: !deployment.next_pm_date
    },
    {
      type: 'string',
      name: 'health',
      label: t`Health`,
      icon: 'status',
      hidden: !isDeployed,
      value_formatter: () => (
        <FleetStatusBadge
          type={FleetStatus.health}
          status={deployment.health}
        />
      )
    },
    {
      type: 'date',
      name: 'last_contact',
      label: t`Last Contact`,
      icon: 'calendar',
      showTime: true,
      hidden: !deployment.last_contact
    }
  ];

  return { left, right };
}

/**
 * Maintenance task fields: the task (left), dates and work (right)
 */
export function taskDetailFields(task: any): FleetDetailFields {
  const left: DetailsField[] = [
    {
      type: 'string',
      name: 'reference',
      label: t`Reference`,
      icon: 'reference',
      copy: true
    },
    {
      type: 'status',
      name: 'status',
      label: t`Status`,
      model: ModelType.maintenancetask
    },
    customStatusField(task, ModelType.maintenancetask),
    {
      type: 'string',
      name: 'task_type',
      label: t`Task Type`,
      icon: 'info',
      value_formatter: () => taskTypeLabel(task.task_type)
    },
    {
      type: 'string',
      name: 'description',
      label: t`Description`,
      icon: 'description',
      hidden: !task.description
    },
    task.device
      ? {
          type: 'link',
          name: 'device',
          label: t`Device`,
          icon: 'serial',
          model: ModelType.stockitem,
          model_formatter: (device: any) =>
            device?.serial ?? task.device_detail?.serial,
          backup_value: task.device_detail?.serial
        }
      : {
          type: 'string',
          name: 'device',
          label: t`Device`,
          icon: 'serial',
          value_formatter: () => t`Not built yet`
        },
    {
      type: 'link',
      name: 'deployment',
      label: t`Deployment`,
      icon: 'fleet_deployment',
      model: ModelType.deployment,
      model_field: 'reference',
      hidden: !task.deployment
    },
    {
      type: 'link',
      name: 'site',
      label: t`Site`,
      icon: 'fleet_site',
      model: ModelType.site,
      hidden: !task.site
    },
    {
      type: 'link',
      name: 'trip',
      label: t`Field Trip`,
      icon: 'fleet_trip',
      model: ModelType.fieldtrip,
      model_formatter: (trip: any) =>
        [trip?.reference, trip?.title].filter(Boolean).join(' · '),
      hidden: !task.trip
    },
    {
      type: 'link',
      name: 'follow_up_of',
      label: t`Follow-up Of`,
      icon: 'fleet_task',
      model: ModelType.maintenancetask,
      model_field: 'reference',
      hidden: !task.follow_up_of
    }
  ];

  const right: DetailsField[] = [
    {
      type: 'date',
      name: 'due_date',
      label: t`Due Date`,
      icon: 'calendar',
      hidden: !task.due_date
    },
    {
      type: 'date',
      name: 'scheduled_date',
      label: t`Scheduled`,
      icon: 'calendar',
      hidden: !task.scheduled_date
    },
    {
      type: 'date',
      name: 'started_at',
      label: t`Started`,
      icon: 'calendar',
      showTime: true,
      hidden: !task.started_at
    },
    {
      type: 'date',
      name: 'completed_at',
      label: t`Completed`,
      icon: 'calendar',
      showTime: true,
      hidden: !task.completed_at
    },
    {
      // Work entered later than it was done
      type: 'date',
      name: 'recorded_at',
      label: t`Recorded`,
      icon: 'calendar',
      showTime: true,
      hidden:
        !task.recorded_at ||
        !task.completed_at ||
        task.recorded_at.slice(0, 10) == task.completed_at.slice(0, 10)
    },
    {
      type: 'string',
      name: 'started_by',
      label: t`Started By`,
      icon: 'user',
      badge: 'user',
      hidden: !task.started_by,
      value_formatter: () => userDisplayName(task.started_by_detail)
    },
    {
      type: 'string',
      name: 'completed_by',
      label: t`Completed By`,
      icon: 'user',
      badge: 'user',
      hidden: !task.completed_by,
      value_formatter: () => userDisplayName(task.completed_by_detail)
    },
    {
      type: 'string',
      name: 'technicians',
      label: t`Technicians`,
      icon: 'user',
      hidden: !task.technicians_detail?.length,
      value_formatter: () => userListNames(task.technicians_detail)
    },
    {
      type: 'number',
      name: 'labour_minutes',
      label: t`Labour (minutes)`,
      icon: 'info',
      hidden: task.labour_minutes == null
    },
    {
      type: 'string',
      name: 'as_found',
      label: t`As Found`,
      icon: 'info',
      hidden: !task.as_found
    },
    {
      type: 'string',
      name: 'summary',
      label: t`Summary`,
      icon: 'info',
      hidden: !task.summary
    }
  ];

  return { left, right };
}

/**
 * Field trip fields: the trip (left), dates and kit (right)
 */
export function tripDetailFields(trip: any): FleetDetailFields {
  const left: DetailsField[] = [
    {
      type: 'string',
      name: 'reference',
      label: t`Reference`,
      icon: 'reference',
      copy: true
    },
    {
      type: 'string',
      name: 'title',
      label: t`Title`,
      icon: 'description'
    },
    {
      type: 'status',
      name: 'status',
      label: t`Status`,
      model: ModelType.fieldtrip
    },
    customStatusField(trip, ModelType.fieldtrip),
    {
      type: 'string',
      name: 'vessel',
      label: t`Vessel`,
      icon: 'fleet_trip',
      hidden: !trip.vessel
    },
    {
      type: 'string',
      name: 'responsible',
      label: t`Responsible`,
      icon: 'responsible',
      badge: 'owner',
      hidden: !trip.responsible,
      value_formatter: () => trip.responsible_detail?.name
    },
    {
      type: 'string',
      name: 'team',
      label: t`Team`,
      icon: 'group',
      hidden: !trip.team_detail?.length,
      value_formatter: () => userListNames(trip.team_detail)
    }
  ];

  const right: DetailsField[] = [
    {
      type: 'date',
      name: 'start_date',
      label: t`Start Date`,
      icon: 'calendar',
      hidden: !trip.start_date
    },
    {
      type: 'date',
      name: 'end_date',
      label: t`End Date`,
      icon: 'calendar',
      hidden: !trip.end_date
    },
    {
      type: 'string',
      name: 'task_count',
      label: t`Tasks`,
      icon: 'fleet_task',
      value_formatter: () =>
        `${trip.task_count ?? 0} (${t`open`}: ${trip.open_task_count ?? 0}, ${t`completed`}: ${trip.completed_task_count ?? 0})`
    },
    {
      type: 'number',
      name: 'kit_line_count',
      label: t`Kit Lines`,
      icon: 'packages'
    },
    {
      type: 'link',
      name: 'kit_location',
      label: t`Kit Location`,
      icon: 'location',
      model: ModelType.stocklocation,
      hidden: !trip.kit_location
    }
  ];

  return { left, right };
}

/**
 * Site fields: the site (left) and its location (right)
 */
export function siteDetailFields(site: any): FleetDetailFields {
  const left: DetailsField[] = [
    {
      type: 'string',
      name: 'reference',
      label: t`Reference`,
      icon: 'reference',
      copy: true
    },
    {
      type: 'string',
      name: 'name',
      label: t`Name`,
      icon: 'name'
    },
    {
      type: 'string',
      name: 'coverage',
      label: t`Coverage`,
      icon: 'info',
      value_formatter: () => <CoverageBadge coverage={site.coverage} />
    },
    {
      type: 'link',
      name: 'client',
      label: t`Client`,
      icon: 'customer',
      model: ModelType.company,
      hidden: !site.client
    },
    {
      type: 'string',
      name: 'project',
      label: t`Project`,
      icon: 'info',
      hidden: !site.project
    },
    {
      type: 'link',
      name: 'active_deployment',
      label: t`Active Deployment`,
      icon: 'fleet_deployment',
      model: ModelType.deployment,
      model_field: 'reference',
      hidden: !site.active_deployment
    }
  ];

  const right: DetailsField[] = [
    {
      type: 'string',
      name: 'location',
      label: t`Location`,
      icon: 'location',
      hidden: site.latitude != null && site.longitude != null,
      value_formatter: () => <Text size='sm' c='orange'>{t`Not set`}</Text>
    },
    {
      type: 'string',
      name: 'latitude',
      label: t`Latitude`,
      icon: 'location',
      hidden: site.latitude == null,
      value_formatter: () => (
        <CoordinateValue value={site.latitude} axis='latitude' />
      )
    },
    {
      type: 'string',
      name: 'longitude',
      label: t`Longitude`,
      icon: 'location',
      hidden: site.longitude == null,
      value_formatter: () => (
        <CoordinateValue value={site.longitude} axis='longitude' />
      )
    },
    {
      type: 'number',
      name: 'depth_m',
      label: t`Depth (m)`,
      icon: 'info',
      hidden: site.depth_m == null
    },
    {
      type: 'number',
      name: 'geofence_radius_m',
      label: t`Geofence Radius (m)`,
      icon: 'info',
      hidden: site.geofence_radius_m == null
    },
    {
      type: 'number',
      name: 'pm_interval_override_days',
      label: t`PM Interval Override (days)`,
      icon: 'calendar',
      hidden: !site.pm_interval_override_days
    },
    {
      type: 'boolean',
      name: 'active',
      label: t`Active`,
      icon: 'active'
    }
  ];

  return { left, right };
}
