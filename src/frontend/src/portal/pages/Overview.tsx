import { t } from '@lingui/core/macro';
import { Paper, Stack, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import { FleetMapView } from '../../fleet/components/FleetMap';
import { OverviewKpis } from '../../fleet/components/OverviewKpis';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { DeploymentTable } from '../../fleet/tables/DeploymentTable';
import { PortalPage } from '../components/PortalPage';
import { useIsPhone } from '../useIsPhone';

/**
 * Portal home (manager): KPI tiles, map of the deployed devices with their
 * health and geofences, open alerts (acknowledge, resolve, create task) and
 * the deployed devices (site, device, health, last data, days deployed,
 * next PM, coverage; filters client, health, PM due, coverage).
 */
export default function Overview(): ReactNode {
  const isPhone = useIsPhone();

  return (
    <PortalPage title={t`Fleet Overview`}>
      <OverviewKpis />
      <Paper withBorder p={4}>
        <FleetMapView height={isPhone ? 280 : 420} />
      </Paper>
      <Stack gap='xs'>
        <Title order={4}>{t`Open Alerts`}</Title>
        <AlertTable openOnly tableName='fleet-portal-overview-alerts' />
      </Stack>
      <Stack gap='xs'>
        <Title order={4}>{t`Deployed Devices`}</Title>
        <DeploymentTable
          mode='active'
          tableName='fleet-portal-overview-deployments'
        />
      </Stack>
    </PortalPage>
  );
}
