import { t } from '@lingui/core/macro';
import {
  Anchor,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { IconEdit, IconLink } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { deviceLinkFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import {
  CoverageBadge,
  FleetStatus,
  FleetStatusBadge
} from '../components/FleetBadges';
import { StreamStatusList, formatAge } from '../components/StreamStatusList';
import { DeploymentTable } from '../tables/DeploymentTable';
import { TaskTable } from '../tables/TaskTable';

/**
 * Fleet information of a stock item: whether it is a fleet device, its
 * platform link and its deployments.
 *
 * The Fleet panel is shown when the part (or its template) is a fleet device
 * type, or when the item already has a platform link or deployments.
 */
export function useStockFleetInfo(stockitem: any) {
  const api = useApi();
  const user = useUserState();
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });

  const enabled =
    !!stockitem?.pk && !!stockitem?.serial && user.hasViewRole(UserRoles.fleet);

  const query = useQuery({
    queryKey: ['fleet-stock-info', stockitem?.pk, deploymentStatus.DEPLOYED],
    enabled: enabled,
    queryFn: async () => {
      const parts = [stockitem.part, stockitem.part_detail?.variant_of].filter(
        Boolean
      );

      const [deviceTypes, links, deployments] = await Promise.all([
        Promise.all(
          parts.map((part: number) =>
            api
              .get(apiUrl(ApiEndpoints.fleet_device_type_list), {
                params: { part: part }
              })
              .then((res) => res.data ?? [])
          )
        ),
        api
          .get(apiUrl(ApiEndpoints.fleet_device_link_list), {
            params: { stock_item: stockitem.pk }
          })
          .then((res) => res.data ?? []),
        api
          .get(apiUrl(ApiEndpoints.fleet_deployment_list), {
            params: { device: stockitem.pk, ordering: '-deployed_at' }
          })
          .then((res) => res.data ?? [])
      ]);

      const deviceType = deviceTypes.flat()[0] ?? null;
      const link = links[0] ?? null;

      return {
        deviceType: deviceType,
        link: link,
        deployments: deployments,
        active:
          deployments.find(
            (dep: any) => dep.status == deploymentStatus.DEPLOYED
          ) ?? null,
        isFleetDevice: !!deviceType || !!link || deployments.length > 0
      };
    }
  });

  return {
    query: query,
    visible: enabled && !!query.data?.isFleetDevice
  };
}

/**
 * Card for the current (deployed) deployment of a device
 */
function CurrentDeploymentCard({
  deployment
}: Readonly<{ deployment: any }>): ReactNode {
  const navigate = useNavigate();

  return (
    <Paper withBorder p='sm'>
      <Stack gap='xs'>
        <Group justify='space-between'>
          <Title order={5}>{t`Current Deployment`}</Title>
          <FleetStatusBadge
            type={FleetStatus.health}
            status={deployment.health}
          />
        </Group>
        <Table>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td>{t`Deployment`}</Table.Td>
              <Table.Td>
                <Anchor
                  size='sm'
                  onClick={() =>
                    navigate(getDetailUrl(ModelType.deployment, deployment.pk))
                  }
                >
                  {deployment.reference}
                </Anchor>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Site`}</Table.Td>
              <Table.Td>
                {deployment.site_detail ? (
                  <Anchor
                    size='sm'
                    onClick={() =>
                      navigate(getDetailUrl(ModelType.site, deployment.site))
                    }
                  >
                    {deployment.site_detail.name}
                  </Anchor>
                ) : (
                  '-'
                )}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Deployed`}</Table.Td>
              <Table.Td>{formatDate(deployment.deployed_at)}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Last Data`}</Table.Td>
              <Table.Td>
                {deployment.last_contact
                  ? `${formatDate(deployment.last_contact, { showTime: true })} (${formatAge(deployment.last_contact)})`
                  : t`Never`}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Next PM`}</Table.Td>
              <Table.Td>
                {deployment.next_pm_date
                  ? formatDate(deployment.next_pm_date)
                  : '-'}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Coverage`}</Table.Td>
              <Table.Td>
                <CoverageBadge coverage={deployment.coverage} />
              </Table.Td>
            </Table.Tr>
            {!!deployment.open_alert_count && (
              <Table.Tr>
                <Table.Td>{t`Open Alerts`}</Table.Td>
                <Table.Td>
                  <Text size='sm' c='red'>
                    {deployment.open_alert_count}
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
        <StreamStatusList deploymentId={deployment.pk} />
      </Stack>
    </Paper>
  );
}

/**
 * "Fleet" panel of a stock item: current deployment, platform link and
 * deployment history
 */
export function StockFleetPanel({
  stockitem,
  info
}: Readonly<{
  stockitem: any;
  info: ReturnType<typeof useStockFleetInfo>;
}>): ReactNode {
  const user = useUserState();
  const data = info.query.data;
  const link = data?.link;

  const createLink = useCreateApiFormModal({
    url: ApiEndpoints.fleet_device_link_list,
    title: t`Link to Data Platform`,
    fields: deviceLinkFields(stockitem.pk),
    initialData: { stock_item: stockitem.pk },
    onFormSuccess: () => info.query.refetch()
  });

  const editLink = useEditApiFormModal({
    url: ApiEndpoints.fleet_device_link_list,
    pk: link?.pk,
    title: t`Edit Platform Link`,
    fields: deviceLinkFields(stockitem.pk),
    onFormSuccess: () => info.query.refetch()
  });

  if (info.query.isLoading || !data) {
    return <Skeleton height={200} />;
  }

  return (
    <>
      {createLink.modal}
      {editLink.modal}
      <Stack gap='sm'>
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing='sm'>
          {data.active ? (
            <CurrentDeploymentCard deployment={data.active} />
          ) : (
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Current Deployment`}</Title>
                <Text size='sm' c='dimmed'>
                  {t`This device is not deployed.`}
                </Text>
              </Stack>
            </Paper>
          )}
          <Paper withBorder p='sm'>
            <Stack gap='xs'>
              <Group justify='space-between'>
                <Title order={5}>{t`Data Platform`}</Title>
                {link ? (
                  <Button
                    size='xs'
                    variant='outline'
                    leftSection={<IconEdit size={16} />}
                    hidden={!user.hasChangeRole(UserRoles.fleet)}
                    onClick={() => editLink.open()}
                  >
                    {t`Edit`}
                  </Button>
                ) : (
                  <Button
                    size='xs'
                    leftSection={<IconLink size={16} />}
                    hidden={!user.hasAddRole(UserRoles.fleet)}
                    onClick={() => createLink.open()}
                  >
                    {t`Link`}
                  </Button>
                )}
              </Group>
              <Table>
                <Table.Tbody>
                  <Table.Tr>
                    <Table.Td>{t`Platform ID`}</Table.Td>
                    <Table.Td>
                      {link?.platform_id ? (
                        link.platform_id
                      ) : (
                        <Text size='sm' c='orange'>{t`Not configured`}</Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                  <Table.Tr>
                    <Table.Td>{t`Firmware`}</Table.Td>
                    <Table.Td>{link?.firmware_version || '-'}</Table.Td>
                  </Table.Tr>
                  {link?.comment && (
                    <Table.Tr>
                      <Table.Td>{t`Comment`}</Table.Td>
                      <Table.Td>{link.comment}</Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
              <Text size='xs' c='dimmed'>
                {t`The platform ID is the device identifier on the data dashboard. Devices without one are not monitored.`}
              </Text>
            </Stack>
          </Paper>
        </SimpleGrid>
        <Title order={5}>{t`Deployment History`}</Title>
        <DeploymentTable
          mode='all'
          params={{ device: stockitem.pk }}
          tableName='fleet-stock-deployments'
        />
        <Title order={5}>{t`Maintenance History`}</Title>
        <TaskTable
          params={{ device: stockitem.pk }}
          device={stockitem.pk}
          tableName='fleet-stock-tasks'
        />
      </Stack>
    </>
  );
}
