import { t } from '@lingui/core/macro';
import { SimpleGrid, Skeleton, Stack } from '@mantine/core';
import type { ReactNode } from 'react';

import useStatusCodes from '../../hooks/UseStatusCodes';
import { FleetStatus, fleetStatusColor } from './FleetBadges';
import { KpiTile, useFleetOverview } from './PipelineKpis';

/**
 * KPI tiles for the fleet overview: deployed devices by health, open alerts
 * by severity, and preventive maintenance due; a second row counts the
 * devices by internal state (docked devices are only counted there)
 */
export function OverviewKpis(): ReactNode {
  const query = useFleetOverview();
  const health = useStatusCodes({ modelType: FleetStatus.health });
  const severity = useStatusCodes({ modelType: FleetStatus.severity });

  // Tile colours of the health and severity counts follow the status codes
  const healthColor = (name: string) =>
    fleetStatusColor(FleetStatus.health, health[name]);
  const severityColor = (name: string) =>
    fleetStatusColor(FleetStatus.severity, severity[name]);

  if (query.isLoading) {
    return <Skeleton height={60} />;
  }

  const data = query.data ?? {};

  return (
    <Stack gap='xs'>
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 11 }} spacing='xs'>
        <KpiTile label={t`Deployed`} value={data.deployed} color='blue' />
        <KpiTile
          label={t`Healthy`}
          value={data.health_ok}
          color={healthColor('OK')}
        />
        <KpiTile
          label={t`Degraded`}
          value={data.health_degraded}
          color={healthColor('DEGRADED')}
        />
        <KpiTile
          label={t`Critical`}
          value={data.health_critical}
          color={healthColor('CRITICAL')}
        />
        <KpiTile
          label={t`No data yet`}
          value={data.health_unknown}
          color={healthColor('UNKNOWN')}
        />
        <KpiTile
          label={t`Critical alerts`}
          value={data.alerts_critical}
          color={severityColor('CRITICAL')}
        />
        <KpiTile
          label={t`Warnings`}
          value={data.alerts_warning}
          color={severityColor('WARNING')}
        />
        <KpiTile
          label={t`PM due in 30 days`}
          value={data.pm_due_30}
          color='orange'
        />
        <KpiTile label={t`PM overdue`} value={data.pm_overdue} color='red' />
        <KpiTile
          label={t`No position`}
          value={data.no_position}
          color='orange'
        />
        <KpiTile label={t`No site`} value={data.no_site} color='gray' />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing='xs'>
        <KpiTile label={t`Active`} value={data.state_active} color='green' />
        <KpiTile
          label={t`Unresponsive`}
          value={data.state_unresponsive}
          color='red'
        />
        <KpiTile
          label={t`Problem acknowledged`}
          value={data.state_problem_acknowledged}
          color='yellow'
        />
        <KpiTile
          label={t`Maintenance overdue`}
          value={data.state_maintenance_overdue}
          color='orange'
        />
        <KpiTile
          label={t`Maintenance scheduled`}
          value={data.state_maintenance_scheduled}
          color='cyan'
        />
        <KpiTile label={t`Docked`} value={data.state_docked} color='grape' />
        <KpiTile
          label={t`Decommissioned`}
          value={data.state_decommissioned}
          color='gray'
        />
      </SimpleGrid>
    </Stack>
  );
}
