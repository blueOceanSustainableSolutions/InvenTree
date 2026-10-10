import { t } from '@lingui/core/macro';
import {
  Badge,
  Group,
  Paper,
  Skeleton,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import { IconStarFilled } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { FleetStatus, FleetStatusBadge } from './FleetBadges';

/**
 * Return how long ago a date was, e.g. "5 min", "3 h", "2 d"
 */
export function formatAge(value?: string | null): string {
  if (!value) {
    return t`Never`;
  }

  const minutes = Math.max(
    0,
    Math.floor((Date.now() - new Date(value).getTime()) / 60000)
  );

  if (minutes < 60) {
    return `${minutes} min`;
  }

  const hours = Math.floor(minutes / 60);

  if (hours < 48) {
    return `${hours} h`;
  }

  return `${Math.floor(hours / 24)} d`;
}

/**
 * One row per data stream of a deployment: essential flag, age of the last
 * data and the stream state (colour)
 */
export function StreamStatusList({
  deploymentId
}: Readonly<{ deploymentId: number }>): ReactNode {
  const api = useApi();

  const query = useQuery({
    queryKey: ['fleet-streams', deploymentId],
    enabled: !!deploymentId,
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_stream_list), {
          params: { deployment: deploymentId, ordering: '-essential' }
        })
        .then((res) => res.data ?? [])
  });

  if (query.isLoading) {
    return <Skeleton height={60} />;
  }

  const streams: any[] = query.data ?? [];

  if (streams.length == 0) {
    return (
      <Text size='sm' c='dimmed'>
        {t`No data streams are monitored for this deployment.`}
      </Text>
    );
  }

  return (
    <Stack gap={4}>
      {streams.map((stream: any) => (
        <Paper key={stream.pk} withBorder p={6}>
          <Group justify='space-between' wrap='nowrap'>
            <Group gap={6} wrap='nowrap'>
              {stream.essential ? (
                <Tooltip label={t`Essential stream`}>
                  <IconStarFilled size={14} color='orange' />
                </Tooltip>
              ) : (
                <span style={{ width: 14 }} />
              )}
              <Stack gap={0}>
                <Text size='sm'>{stream.name || stream.key}</Text>
                {stream.name && (
                  <Text size='xs' c='dimmed'>
                    {stream.key}
                  </Text>
                )}
              </Stack>
            </Group>
            <Group gap={6} wrap='nowrap'>
              <Tooltip
                label={
                  stream.last_seen
                    ? formatDate(stream.last_seen, { showTime: true })
                    : t`No data received yet`
                }
              >
                <Text size='xs' c='dimmed'>
                  {formatAge(stream.last_seen)}
                </Text>
              </Tooltip>
              {stream.enabled ? (
                <FleetStatusBadge
                  type={FleetStatus.stream}
                  status={stream.state}
                />
              ) : (
                <Badge color='gray' variant='outline' size='sm'>
                  {t`Disabled`}
                </Badge>
              )}
            </Group>
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}
