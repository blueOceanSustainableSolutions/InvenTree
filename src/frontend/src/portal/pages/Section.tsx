import type { ReactNode } from 'react';
import { useMemo } from 'react';
import { useLocation } from 'react-router-dom';

import { fleetSections } from '../../fleet/FleetSections';
import { PortalPage } from '../components/PortalPage';
import { MainUiRedirect } from '../navigation';

/**
 * A portal screen for one fleet section (fleet/FleetSections.tsx): the same
 * content as the matching panel of the main UI fleet index. A path which is
 * no section is a page of the main UI.
 */
export default function Section(): ReactNode {
  const location = useLocation();

  const section = useMemo(() => {
    const path = location.pathname.replace(/\/+$/, '') || '/';

    return fleetSections().find((item) => item.path == path);
  }, [location.pathname]);

  if (!section) {
    return <MainUiRedirect />;
  }

  return (
    <PortalPage key={section.name} title={section.label}>
      {section.content}
    </PortalPage>
  );
}
