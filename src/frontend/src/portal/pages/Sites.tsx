import { t } from '@lingui/core/macro';
import type { ReactNode } from 'react';

import { SiteTable } from '../../fleet/tables/SiteTable';
import { PortalPage } from '../components/PortalPage';

/**
 * Sites (locations with a nickname). A site is optional for a deployment:
 * devices without one are listed on the overview and the pipeline.
 */
export default function Sites(): ReactNode {
  return (
    <PortalPage title={t`Sites`}>
      <SiteTable />
    </PortalPage>
  );
}
