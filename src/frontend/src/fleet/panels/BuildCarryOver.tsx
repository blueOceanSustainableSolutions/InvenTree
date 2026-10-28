import { t } from '@lingui/core/macro';
import { Alert, Anchor, Badge, List, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import { useApi } from '../../contexts/ApiContext';
import { formatDecimal } from '../../defaults/formatters';

/**
 * Link of a carry-over line: its swap task, else its deployment
 */
function carryOverLink(line: any): string {
  return line.swap_task
    ? getDetailUrl(ModelType.maintenancetask, line.swap_task)
    : getDetailUrl(ModelType.deployment, line.deployment);
}

/**
 * Badge on a build order line covered by a carry-over (pending), or which
 * received carried over stock (moved): "From HT-S-016 (MT-0012)"
 */
function CarryOverBadge({ lines }: Readonly<{ lines: any[] }>): ReactNode {
  const navigate = useNavigate();
  const line = lines[0];
  const pending = lines.some((row) => row.status == 'PENDING');
  const quantity = String(
    formatDecimal(lines.reduce((sum, row) => sum + Number(row.quantity), 0)) ??
      ''
  );

  const source = line.source_device_serial || line.source_build_reference;
  const task = line.swap_task_reference || line.deployment_reference;
  const url = carryOverLink(line);

  return (
    <Anchor
      href={url}
      onClick={(event: any) => {
        event.preventDefault();
        event.stopPropagation();
        navigateToLink(url, navigate, event);
      }}
    >
      <Badge
        size='sm'
        variant='light'
        color={pending ? 'blue' : 'green'}
        style={{ cursor: 'pointer' }}
        title={
          pending
            ? t`Covered by the carry-over of a swap: ${quantity} (not allocated)`
            : t`Carried over in a swap: ${quantity}`
        }
      >
        {pending
          ? t`From ${source} (${task})`
          : t`Carried over from ${source} (${task})`}
      </Badge>
    </Anchor>
  );
}

/**
 * The carry-over lines of a build order (fleet swaps): badges per build line,
 * and the released lines which left the completed build order short.
 */
export function useBuildCarryOver(buildId?: number, enabled = true) {
  const api = useApi();

  const query = useQuery({
    queryKey: ['fleet-build-carry-over', buildId],
    enabled: !!buildId && enabled,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_carry_over_list), {
          params: { build: buildId }
        })
        .then((res) => res.data ?? [])
        .catch(() => [])
  });

  return useMemo(() => {
    const lines: any[] = query.data ?? [];
    const byLine: Record<number, any[]> = {};

    for (const line of lines) {
      if (line.target_line && ['PENDING', 'MOVED'].includes(line.status)) {
        byLine[line.target_line] = [...(byLine[line.target_line] ?? []), line];
      }
    }

    const badges: Record<number, ReactNode> = Object.fromEntries(
      Object.entries(byLine).map(([key, rows]) => [
        key,
        <CarryOverBadge key={key} lines={rows} />
      ])
    );

    return {
      badges: badges,
      shortages: lines.filter((line) => line.short),
      refetch: query.refetch
    };
  }, [query.data, query.refetch]);
}

/**
 * Warning on a completed build order whose carry-over was released: the swap
 * did not happen, so these lines are no longer covered (the build order is not
 * reopened).
 */
export function BuildCarryOverWarning({
  shortages
}: Readonly<{ shortages: any[] }>): ReactNode {
  const navigate = useNavigate();

  if (!shortages?.length) {
    return null;
  }

  return (
    <Alert
      color='orange'
      icon={<IconAlertTriangle />}
      title={t`Carry-over released: parts missing`}
    >
      <Stack gap='xs'>
        <Text size='sm'>
          {t`This build order was completed with parts which a swap would carry over from another device. The swap did not happen, so these parts are not in this build order.`}
        </Text>
        <List size='sm'>
          {shortages.map((line: any) => {
            const url = carryOverLink(line);

            return (
              <List.Item key={line.pk}>
                {formatDecimal(line.quantity)} x {line.part_detail?.full_name} (
                {t`from`} {line.source_device_serial},{' '}
                <Anchor
                  href={url}
                  onClick={(event: any) => {
                    event.preventDefault();
                    navigateToLink(url, navigate, event);
                  }}
                >
                  {line.swap_task_reference || line.deployment_reference}
                </Anchor>
                )
              </List.Item>
            );
          })}
        </List>
      </Stack>
    </Alert>
  );
}
