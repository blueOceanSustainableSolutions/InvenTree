import type { ReactNode } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import { type InstanceRenderInterface, RenderInlineModel } from './Instance';
import { StatusRenderer } from './StatusRenderer';

/**
 * Inline rendering of a single fleet Site instance
 */
export function RenderFleetSite(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  return (
    <RenderInlineModel
      {...props}
      primary={instance.name}
      secondary={instance.reference}
      url={props.link ? getDetailUrl(ModelType.site, instance.pk) : undefined}
    />
  );
}

/**
 * Inline rendering of a single Deployment instance
 */
export function RenderDeployment(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  const secondary = [
    instance.site_detail?.name,
    instance.device_detail?.serial ? `#${instance.device_detail.serial}` : null
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <RenderInlineModel
      {...props}
      primary={instance.reference}
      secondary={secondary}
      suffix={StatusRenderer({
        status: instance.status_custom_key || instance.status,
        type: ModelType.deployment
      })}
      url={
        props.link ? getDetailUrl(ModelType.deployment, instance.pk) : undefined
      }
    />
  );
}

/**
 * Inline rendering of a single FleetDeviceType instance
 */
export function RenderFleetDeviceType(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  return (
    <RenderInlineModel
      {...props}
      primary={instance.part_detail?.full_name ?? instance.part_name}
      image={instance.part_detail?.thumbnail || instance.part_detail?.image}
      url={
        props.link
          ? getDetailUrl(ModelType.fleetdevicetype, instance.pk)
          : undefined
      }
    />
  );
}

/**
 * Inline rendering of a single fleet Alert instance
 */
export function RenderFleetAlert(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  return (
    <RenderInlineModel
      {...props}
      primary={instance.reference}
      secondary={instance.message}
      suffix={StatusRenderer({
        status: instance.status_custom_key || instance.status,
        type: ModelType.alert
      })}
      url={
        props.link && instance.deployment
          ? getDetailUrl(ModelType.deployment, instance.deployment)
          : undefined
      }
    />
  );
}

/**
 * Inline rendering of a single fleet MaintenanceTask instance
 */
export function RenderFleetTask(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  return (
    <RenderInlineModel
      {...props}
      primary={instance.reference}
      secondary={instance.display_name ?? instance.description}
      suffix={StatusRenderer({
        status: instance.status_custom_key || instance.status,
        type: ModelType.maintenancetask
      })}
      url={
        props.link
          ? getDetailUrl(ModelType.maintenancetask, instance.pk)
          : undefined
      }
    />
  );
}

/**
 * Inline rendering of a single fleet FieldTrip instance
 */
export function RenderFleetTrip(
  props: Readonly<InstanceRenderInterface>
): ReactNode {
  const { instance } = props;

  const secondary = [instance.title, instance.start_date]
    .filter(Boolean)
    .join(' · ');

  return (
    <RenderInlineModel
      {...props}
      primary={instance.reference}
      secondary={secondary}
      suffix={StatusRenderer({
        status: instance.status_custom_key || instance.status,
        type: ModelType.fieldtrip
      })}
      url={
        props.link ? getDetailUrl(ModelType.fieldtrip, instance.pk) : undefined
      }
    />
  );
}
