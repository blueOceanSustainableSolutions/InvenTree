import { t } from '@lingui/core/macro';
import type { ReactNode } from 'react';

import { TripTable } from '../../fleet/tables/TripTable';
import { PortalPage } from '../components/PortalPage';

/**
 * Field trips (manager): filters open, status and "mine"; "New Field Trip".
 * Trips are also created from a selection of tasks (Plan) or of ready
 * deployments (Pipeline).
 */
export default function Trips(): ReactNode {
  return (
    <PortalPage title={t`Field Trips`}>
      <TripTable tableName='fleet-portal-trips' />
    </PortalPage>
  );
}
