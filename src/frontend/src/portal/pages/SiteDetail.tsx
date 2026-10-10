import { t } from '@lingui/core/macro';
import {
  Anchor,
  Group,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Text
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconAnchor,
  IconMap,
  IconTool
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { PanelType } from '@lib/types/Panel';
import InstanceDetail from '../../components/nav/InstanceDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { CoverageBadge } from '../../fleet/components/FleetBadges';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { KpiTile } from '../../fleet/components/PipelineKpis';
import {
  detailFieldsByName,
  siteDetailFields
} from '../../fleet/details/FleetDetailFields';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { useInstance } from '../../hooks/UseInstance';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { PortalDetailFields } from '../components/PortalDetails';
import { PortalPage } from '../components/PortalPage';
import { PortalTabs } from '../components/PortalTabs';
import { useIsPhone } from '../useIsPhone';

/** Length of the statistics window */
const WINDOW_DAYS = 365;
const DAY_MS = 86400000;

/**
 * Share of the last 12 months during which the site had a device in the
 * water (union of the deployed periods of its deployments), in percent
 */
export function deployedPercent(deployments: any[], now = Date.now()): number {
  const start = now - WINDOW_DAYS * DAY_MS;

  const periods = deployments
    .filter((dep) => !!dep.deployed_at)
    .map((dep) => [
      Math.max(new Date(dep.deployed_at).getTime(), start),
      Math.min(
        dep.recovered_at ? new Date(dep.recovered_at).getTime() : now,
        now
      )
    ])
    .filter(([from, to]) => to > from)
    .sort((a, b) => a[0] - b[0]);

  let total = 0;
  let end = start;

  for (const [from, to] of periods) {
    if (to > end) {
      total += to - Math.max(from, end);
      end = to;
    }
  }

  return Math.round((100 * total) / (WINDOW_DAYS * DAY_MS));
}

/** The devices of a site over time: one row per deployment, newest first */
function DeviceHistory({
  deployments
}: Readonly<{ deployments: any[] }>): ReactNode {
  const navigate = useNavigate();

  if (deployments.length == 0) {
    return <Text c='dimmed'>{t`No deployments at this site yet.`}</Text>;
  }

  return (
    <Stack gap='xs'>
      {deployments.map((dep: any) => (
        <Paper key={dep.pk} withBorder p='sm'>
          <Group justify='space-between' gap='xs'>
            <Stack gap={0}>
              <Anchor
                fw={700}
                onClick={() => navigate(`/deployment/${dep.pk}`)}
              >
                {dep.reference}
              </Anchor>
              <Text size='sm'>
                {dep.device_detail
                  ? `${dep.device_detail.part_name} #${dep.device_detail.serial}`
                  : dep.device_type_detail?.part_name}
              </Text>
              <Text size='xs' c='dimmed'>
                {dep.deployed_at
                  ? `${formatDate(dep.deployed_at)} - ${
                      dep.recovered_at ? formatDate(dep.recovered_at) : t`now`
                    }`
                  : dep.target_date
                    ? `${t`Target Date`}: ${formatDate(dep.target_date)}`
                    : t`Not deployed yet`}
              </Text>
            </Stack>
            <StatusRenderer
              status={dep.status_custom_key || dep.status}
              type={ModelType.deployment}
            />
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}

/**
 * Site screen (manager): details, time with a device in the water and
 * visits over the last 12 months, the devices over time, maintenance
 * visits, alerts, map, files and notes.
 */
export default function SiteDetail(): ReactNode {
  const { id } = useParams();
  const api = useApi();
  const navigate = useNavigate();
  const isPhone = useIsPhone();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });

  const { instance: site, instanceQuery } = useInstance({
    endpoint: ApiEndpoints.fleet_site_list,
    pk: id,
    hasPrimaryKey: true
  });

  const deploymentsQuery = useQuery({
    queryKey: ['fleet-portal-site-deployments', id],
    enabled: !!id,
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_deployment_list), {
          params: { site: id, ordering: '-creation_date' }
        })
        .then((response) => response.data ?? [])
  });

  const visitsQuery = useQuery({
    queryKey: ['fleet-portal-site-visits', id, taskStatus.COMPLETED],
    enabled: !!id && taskStatus.COMPLETED !== undefined,
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_list), {
          params: { site: id, status: taskStatus.COMPLETED }
        })
        .then((response) => response.data ?? [])
  });

  const stats = useMemo(() => {
    const since = Date.now() - WINDOW_DAYS * DAY_MS;
    const visits = (visitsQuery.data ?? []).filter(
      (task: any) =>
        task.completed_at && new Date(task.completed_at).getTime() >= since
    );

    return {
      deployedPercent: deployedPercent(deploymentsQuery.data ?? []),
      visits: visits.length,
      devices: (deploymentsQuery.data ?? []).filter(
        (dep: any) => !!dep.deployed_at
      ).length
    };
  }, [deploymentsQuery.data, visitsQuery.data]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'devices',
        label: t`Devices`,
        icon: <IconAnchor />,
        content: deploymentsQuery.isLoading ? (
          <Skeleton h={120} />
        ) : (
          <DeviceHistory deployments={deploymentsQuery.data ?? []} />
        )
      },
      {
        name: 'visits',
        label: t`Visits`,
        icon: <IconTool />,
        content: site.pk ? (
          <TaskTable
            params={{ site: site.pk }}
            tableName='fleet-portal-site-tasks'
          />
        ) : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        content: site.pk ? (
          <AlertTable
            params={{ site: site.pk }}
            tableName='fleet-portal-site-alerts'
          />
        ) : null
      },
      {
        name: 'map',
        label: t`Map`,
        icon: <IconMap />,
        hidden: !site.active_deployment,
        content: site.pk ? (
          <Paper withBorder p={4}>
            <FleetMapView
              params={{ site: site.pk }}
              height={isPhone ? 280 : 420}
            />
          </Paper>
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.site,
        model_id: site.pk
      }),
      NotesPanel({
        model_type: ModelType.site,
        model_id: site.pk,
        has_note: !!site.notes
      })
    ],
    [site, deploymentsQuery, isPhone]
  );

  // The shared field definitions (the portal shows some of them)
  const siteFields = detailFieldsByName(siteDetailFields(site));

  return (
    <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
      <PortalPage
        title={site.name ?? ''}
        subtitle={[site.reference, site.project].filter(Boolean).join(' · ')}
        back='/sites'
        badges={[<CoverageBadge key='coverage' coverage={site.coverage} />]}
      >
        <Paper withBorder p='sm'>
          <SimpleGrid cols={{ base: 2, sm: 4 }} spacing='sm'>
            <PortalDetailFields
              item={site}
              fields={[
                {
                  ...siteFields.active_deployment,
                  label: t`Current Deployment`,
                  hidden: false,
                  value_formatter: () =>
                    site.active_deployment ? (
                      <Anchor
                        size='sm'
                        onClick={() =>
                          navigate(`/deployment/${site.active_deployment}`)
                        }
                      >
                        {site.active_deployment_reference}
                      </Anchor>
                    ) : (
                      <Text size='sm' c='dimmed'>{t`None`}</Text>
                    )
                },
                {
                  ...siteFields.client,
                  hidden: false,
                  value_formatter: () => site.client_detail?.name
                },
                {
                  type: 'string',
                  name: 'position',
                  label: t`Position`,
                  value_formatter: () =>
                    site.latitude != null && site.longitude != null
                      ? `${site.latitude}, ${site.longitude}`
                      : undefined
                },
                siteFields.depth_m
              ]}
            />
          </SimpleGrid>
        </Paper>
        <SimpleGrid cols={{ base: 3 }} spacing='xs'>
          <KpiTile
            label={t`Deployed time, % of 12 months`}
            value={stats.deployedPercent}
            color='green'
          />
          <KpiTile
            label={t`Visits (12 months)`}
            value={stats.visits}
            color='blue'
          />
          <KpiTile label={t`Devices`} value={stats.devices} color='blue' />
        </SimpleGrid>
        <PortalTabs panels={panels} />
      </PortalPage>
    </InstanceDetail>
  );
}
