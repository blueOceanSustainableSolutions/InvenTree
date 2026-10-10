import { t } from '@lingui/core/macro';
import {
  IconAlertTriangle,
  IconCalendarEvent,
  IconClipboardList,
  IconDashboard,
  IconMapPin,
  IconSailboat,
  IconTimeline
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useEffect } from 'react';
import { Navigate, useLocation, useParams } from 'react-router-dom';

/** One entry of the portal navigation (side nav, bottom tab bar) */
export type PortalNavItem = {
  path: string;
  label: string;
  icon: ReactNode;
  /** Shown in the bottom tab bar on phones (the others are under "More") */
  primary: boolean;
};

/** The screens of the portal, in navigation order */
export function portalNavItems(): PortalNavItem[] {
  return [
    {
      path: '/',
      label: t`Overview`,
      icon: <IconDashboard />,
      primary: true
    },
    {
      path: '/my',
      label: t`My Work`,
      icon: <IconClipboardList />,
      primary: true
    },
    {
      path: '/pipeline',
      label: t`Pipeline`,
      icon: <IconTimeline />,
      primary: false
    },
    {
      path: '/plan',
      label: t`Plan`,
      icon: <IconCalendarEvent />,
      primary: false
    },
    {
      path: '/trips',
      label: t`Trips`,
      icon: <IconSailboat />,
      primary: true
    },
    {
      path: '/alerts',
      label: t`Alerts`,
      icon: <IconAlertTriangle />,
      primary: true
    },
    {
      path: '/sites',
      label: t`Sites`,
      icon: <IconMapPin />,
      primary: false
    }
  ];
}

/** Is a navigation item the current screen (or one of its detail pages)? */
export function isActivePath(itemPath: string, pathname: string): boolean {
  if (itemPath == '/') {
    return pathname == '/';
  }

  // Detail pages belong to their list (e.g. /trips/3 to /trips)
  return pathname == itemPath || pathname.startsWith(`${itemPath}/`);
}

/** Base path of the main InvenTree UI (e.g. 'web') */
export function getMainBaseUrl(): string {
  return (window.INVENTREE_SETTINGS as any)?.main_base_url || 'web';
}

/** Absolute URL of a page of the main UI, e.g. '/stock/item/3/' */
export function mainUiUrl(path = ''): string {
  return `/${getMainBaseUrl()}${path}`;
}

/** Main UI fleet index panels which have their own portal screen */
const INDEX_PANELS: Record<string, string> = {
  overview: '/',
  deployments: '/',
  pipeline: '/pipeline',
  alerts: '/alerts',
  maintenance: '/plan',
  calendar: '/plan',
  trips: '/trips',
  sites: '/sites'
};

/**
 * Shared components link to the fleet pages of the main UI (e.g. table rows
 * open `/fleet/deployment/3/`). Inside the portal (base path /fleet/) such a
 * link arrives as `/fleet/deployment/3/` or `/deployment/3/`; these routes
 * send it to the matching portal screen.
 */
export function StripFleetPrefix(): ReactNode {
  const location = useLocation();
  const params = useParams();

  return <Navigate to={`/${params['*'] ?? ''}${location.search}`} replace />;
}

/** `/index/<panel>` (main UI fleet index) -> the matching portal screen */
export function IndexRedirect(): ReactNode {
  const { panel } = useParams();

  if (panel && !(panel in INDEX_PANELS)) {
    return <MainUiRedirect />;
  }

  return <Navigate to={INDEX_PANELS[panel ?? 'overview']} replace />;
}

/** `/trip/<id>` and `/site/<id>` (main UI) -> `/trips/<id>`, `/sites/<id>` */
export function DetailRedirect({ to }: Readonly<{ to: string }>): ReactNode {
  const { id } = useParams();

  return <Navigate to={`${to}/${id}`} replace />;
}

/**
 * Any other path is a page of the main UI (a stock item, a part, a build
 * order, a device type...): leave the portal and open it there.
 */
export function MainUiRedirect(): ReactNode {
  const location = useLocation();

  useEffect(() => {
    let path = location.pathname;

    // Fleet pages without a portal screen live under /fleet/ in the main UI
    if (path.startsWith('/device-type/') || path.startsWith('/index/')) {
      path = `/fleet${path}`;
    }

    window.location.replace(mainUiUrl(`${path}${location.search}`));
  }, [location]);

  return null;
}
