import { t } from '@lingui/core/macro';
import {
  IconAlertTriangle,
  IconAnchor,
  IconCalendarEvent,
  IconClipboardList,
  IconDashboard,
  IconMapPin,
  IconRadar,
  IconSailboat,
  IconTimeline,
  IconTool
} from '@tabler/icons-react';
import type { ReactNode } from 'react';

/**
 * The sections of the fleet module, without their content. The same list is
 * shown in the main UI (/web/fleet/index/<name>, as panels) and in the Fleet
 * Portal (/fleet<path>, as screens); the content is in FleetSections.tsx.
 *
 * This module imports no tables: the portal's routing loads it before the
 * locale is active, and the table modules call translations when they load.
 */
export type FleetSectionInfo = {
  /** Panel name in the main UI */
  name: string;
  /** Screen path in the portal */
  path: string;
  label: string;
  icon: ReactNode;
  /** Shown in the bottom tab bar on phones (the others are under "More") */
  primary: boolean;
};

/** The sections of the fleet module, in navigation order */
export function fleetSectionList(): FleetSectionInfo[] {
  return [
    {
      name: 'overview',
      path: '/',
      label: t`Overview`,
      icon: <IconDashboard />,
      primary: true
    },
    {
      name: 'my-work',
      path: '/my',
      label: t`My Work`,
      icon: <IconClipboardList />,
      primary: true
    },
    {
      name: 'alerts',
      path: '/alerts',
      label: t`Alerts`,
      icon: <IconAlertTriangle />,
      primary: true
    },
    {
      name: 'plan',
      path: '/plan',
      label: t`Plan`,
      icon: <IconTimeline />,
      primary: false
    },
    {
      name: 'deployments',
      path: '/deployments',
      label: t`Deployments`,
      icon: <IconAnchor />,
      primary: false
    },
    {
      name: 'maintenance',
      path: '/maintenance',
      label: t`Maintenance`,
      icon: <IconTool />,
      primary: false
    },
    {
      name: 'trips',
      path: '/trips',
      label: t`Trips`,
      icon: <IconSailboat />,
      primary: true
    },
    {
      name: 'calendar',
      path: '/calendar',
      label: t`Calendar`,
      icon: <IconCalendarEvent />,
      primary: false
    },
    {
      name: 'sites',
      path: '/sites',
      label: t`Sites`,
      icon: <IconMapPin />,
      primary: false
    },
    {
      name: 'device-types',
      path: '/device-types',
      label: t`Device Types`,
      icon: <IconRadar />,
      primary: false
    }
  ];
}
