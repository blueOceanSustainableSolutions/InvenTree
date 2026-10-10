import { t } from '@lingui/core/macro';
import { Paper, SimpleGrid, Skeleton, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';

/**
 * A single KPI tile (number and label)
 */
export function KpiTile({
  label,
  value,
  color
}: Readonly<{ label: string; value?: number; color?: string }>): ReactNode {
  return (
    <Paper withBorder p='xs'>
      <Stack gap={0} align='center'>
        <Text size='xl' fw={700} c={value ? color : 'dimmed'}>
          {value ?? '-'}
        </Text>
        <Text size='xs' c='dimmed' ta='center'>
          {label}
        </Text>
      </Stack>
    </Paper>
  );
}

/**
 * Query the KPI counts of the fleet overview endpoint
 */
export function useFleetOverview() {
  const api = useApi();

  return useQuery({
    queryKey: ['fleet-overview'],
    refetchOnMount: true,
    queryFn: () =>
      api.get(apiUrl(ApiEndpoints.fleet_overview)).then((res) => res.data)
  });
}

/**
 * KPI tiles for the deployment pipeline (from the fleet overview endpoint)
 */
export function PipelineKpis(): ReactNode {
  const query = useFleetOverview();

  if (query.isLoading) {
    return <Skeleton height={60} />;
  }

  const data = query.data ?? {};

  return (
    <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing='xs'>
      <KpiTile label={t`Planned`} value={data.planned} />
      <KpiTile
        label={t`In production`}
        value={data.in_production}
        color='blue'
      />
      <KpiTile label={t`Ready`} value={data.ready} color='cyan' />
      <KpiTile label={t`Scheduled`} value={data.scheduled} color='cyan' />
      <KpiTile
        label={t`To deploy in 30 days`}
        value={data.to_deploy_30}
        color='orange'
      />
      <KpiTile label={t`Unscheduled`} value={data.unscheduled} color='red' />
      <KpiTile label={t`Deployed`} value={data.deployed} color='green' />
    </SimpleGrid>
  );
}

/**
 * KPI tiles for maintenance (from the fleet overview endpoint)
 */
export function MaintenanceKpis(): ReactNode {
  const query = useFleetOverview();

  if (query.isLoading) {
    return <Skeleton height={60} />;
  }

  const data = query.data ?? {};

  return (
    <SimpleGrid cols={{ base: 2, sm: 5 }} spacing='xs'>
      <KpiTile label={t`Open tasks`} value={data.tasks_open} color='blue' />
      <KpiTile
        label={t`In progress`}
        value={data.tasks_in_progress}
        color='cyan'
      />
      <KpiTile
        label={t`Overdue tasks`}
        value={data.tasks_overdue}
        color='red'
      />
      <KpiTile
        label={t`PM due in 30 days`}
        value={data.pm_due_30}
        color='orange'
      />
      <KpiTile label={t`PM overdue`} value={data.pm_overdue} color='red' />
    </SimpleGrid>
  );
}
