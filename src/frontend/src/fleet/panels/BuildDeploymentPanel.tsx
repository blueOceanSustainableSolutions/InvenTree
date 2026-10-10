import { t } from '@lingui/core/macro';
import { Alert, Anchor, Group, Paper, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { RiskBadges } from '../components/FleetBadges';

/**
 * Return the fleet deployment linked to a build order (if any)
 */
export function useBuildDeployment(buildId?: number, enabled = true) {
  const api = useApi();

  const query = useQuery({
    queryKey: ['fleet-build-deployment', buildId],
    enabled: !!buildId && enabled,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_deployment_list), {
          params: { build: buildId, risks: true }
        })
        .then((res) => res.data?.[0] ?? null)
        .catch(() => null)
  });

  return query.data ?? null;
}

/**
 * Deployment plan of a fleet build order: site, target date and risks
 */
export function BuildDeploymentPanel({
  deployment
}: Readonly<{ deployment: any }>): ReactNode {
  const navigate = useNavigate();

  if (!deployment) {
    return null;
  }

  const url = getDetailUrl(ModelType.deployment, deployment.pk);

  return (
    <Paper withBorder p='sm'>
      <Stack gap='xs'>
        <Group justify='space-between'>
          <Anchor
            href={url}
            onClick={(event: any) => {
              event.preventDefault();
              navigateToLink(url, navigate, event);
            }}
          >
            <Text fw={700}>{deployment.reference}</Text>
          </Anchor>
          <StatusRenderer
            status={deployment.status_custom_key || deployment.status}
            type={ModelType.deployment}
          />
        </Group>
        <Table>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td>{t`Site`}</Table.Td>
              <Table.Td>
                {deployment.site_detail
                  ? `${deployment.site_detail.name} (${deployment.site_detail.reference})`
                  : t`Not set`}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Deployment Date`}</Table.Td>
              <Table.Td>
                {deployment.target_date
                  ? formatDate(deployment.target_date)
                  : t`Not set`}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Device`}</Table.Td>
              <Table.Td>
                {deployment.device_detail
                  ? `#${deployment.device_detail.serial}`
                  : t`Not built yet`}
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        {deployment.risks?.length > 0 ? (
          <Stack gap={4}>
            <Text size='sm' fw={500}>{t`Readiness risks`}</Text>
            <RiskBadges risks={deployment.risks} />
          </Stack>
        ) : (
          <Alert color='green'>{t`No readiness risks`}</Alert>
        )}
      </Stack>
    </Paper>
  );
}
