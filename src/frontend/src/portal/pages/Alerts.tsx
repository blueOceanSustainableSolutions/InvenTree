import { t } from '@lingui/core/macro';
import type { ReactNode } from 'react';

import { AlertTable } from '../../fleet/tables/AlertTable';
import { PortalPage } from '../components/PortalPage';

/**
 * All fleet alerts with their filters (status, severity, type, ...); rows can
 * be acknowledged, resolved or turned into a maintenance task.
 */
export default function Alerts(): ReactNode {
  return (
    <PortalPage title={t`Alerts`}>
      <AlertTable tableName='fleet-portal-alerts' />
    </PortalPage>
  );
}
