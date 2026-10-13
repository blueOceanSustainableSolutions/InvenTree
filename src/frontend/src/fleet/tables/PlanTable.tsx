import { t } from '@lingui/core/macro';
import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';
import { IconCalendarEvent } from '@tabler/icons-react';
import { useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import type { RowAction } from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { formatDate } from '../../defaults/formatters';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { FleetStatus, FleetStatusBadge, RiskBadges } from '../components/FleetBadges';
import { useTripSelectionActions } from '../components/TripSelection';
import {
  useDeploymentActions,
  useNewDeploymentForm
} from '../hooks/DeploymentActions';

/** Badge for the kind of a plan row */
function KindBadge({ kind }: Readonly<{ kind: string }>) {
  return kind == 'deployment' ? (
    <Badge color='blue' variant='light' size='sm'>
      {t`Deployment`}
    </Badge>
  ) : (
    <Badge color='teal' variant='light' size='sm'>
      {t`Maintenance`}
    </Badge>
  );
}

/**
 * The fleet plan: one list of the deployments to do (devices which are not
 * deployed: in production, built, or on their way back) and the deployed
 * devices to maintain.
 *
 * The order comes from the server: first the next planning-horizon days
 * (overdue first) by date, then the rows without a date, then the later
 * dates. Rows on the same date (and the rows without a date) go by
 * readiness: ready, in production, planned, then by build target date.
 * Rows can be ticked and grouped into a field trip ("Create Field Trip" /
 * "Add to Trip").
 */
export function PlanTable({
  params = {},
  tableName = 'fleet-plan'
}: Readonly<{
  params?: Record<string, any>;
  tableName?: string;
}>) {
  const table = useTable(tableName);
  const user = useUserState();
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });

  const tripSelection = useTripSelectionActions({
    table: table,
    kind: 'deployments',
    enabled: true
  });

  const newDeployment = useNewDeploymentForm({
    site: params.site,
    onSuccess: () => table.refreshTable()
  });

  // "Set Site and Date" on a deployment row
  const [selected, setSelected] = useState<any>({});

  const rowDeploymentActions = useDeploymentActions({
    deployment: selected,
    onUpdate: () => table.refreshTable()
  });

  // A row which can be put on a trip, if not already on one: a deployed
  // device (its maintenance), or a device to deploy, built or not (a device
  // which is not built yet can only be deployed once it is ready)
  const canSelect = useCallback(
    (record: any): boolean =>
      !record.trip &&
      (record.kind == 'maintenance' ||
        [
          deploymentStatus.PLANNED,
          deploymentStatus.IN_PRODUCTION,
          deploymentStatus.READY
        ].includes(record.status)),
    [deploymentStatus]
  );

  const rowActions = useCallback(
    (record: any): RowAction[] => [
      {
        title: t`Set Site and Date`,
        tooltip: t`Where and when the device is deployed`,
        icon: <IconCalendarEvent />,
        color: 'blue',
        hidden: record.kind != 'deployment' || !user.hasChangeRole(UserRoles.fleet),
        onClick: () => {
          setSelected({ pk: record.deployment, site: record.site });
          rowDeploymentActions.openPlan();
        }
      }
    ],
    [user, rowDeploymentActions]
  );

  const today = new Date().toISOString().slice(0, 10);

  const columns: TableColumn[] = useMemo(
    () => [
      {
        accessor: 'kind',
        title: t`Type`,
        sortable: false,
        switchable: false,
        render: (record: any) => <KindBadge kind={record.kind} />
      },
      {
        accessor: 'date',
        title: t`Date`,
        sortable: false,
        switchable: false,
        render: (record: any) =>
          record.date ? (
            <Text
              size='sm'
              c={
                record.date < today
                  ? 'red'
                  : record.group == 'later'
                    ? 'dimmed'
                    : undefined
              }
            >
              {formatDate(record.date)}
            </Text>
          ) : (
            <Text size='sm' c='dimmed'>{t`No date`}</Text>
          )
      },
      {
        accessor: 'device',
        title: t`Device`,
        sortable: false,
        render: (record: any) => (
          <Stack gap={0}>
            <Text size='sm'>
              {record.serial ? `#${record.serial}` : record.reference}
            </Text>
            <Text size='xs' c='dimmed'>
              {record.part_name}
            </Text>
          </Stack>
        )
      },
      {
        accessor: 'site_name',
        title: t`Site`,
        sortable: false,
        render: (record: any) =>
          record.site_name ?? <Text size='sm' c='dimmed'>{t`Not set`}</Text>
      },
      {
        accessor: 'status',
        title: t`Status`,
        sortable: false,
        render: (record: any) =>
          record.kind == 'deployment' ? (
            <Group gap={4} wrap='nowrap'>
              <StatusRenderer
                status={record.status}
                type={ModelType.deployment}
              />
              {record.return_order && (
                <Tooltip
                  label={t`On its way back on return order` + ` ${record.return_order_reference}`}
                >
                  <Badge color='yellow' variant='light' size='sm'>
                    {t`Returning`}
                  </Badge>
                </Tooltip>
              )}
            </Group>
          ) : (
            <FleetStatusBadge type={FleetStatus.health} status={record.health} />
          )
      },
      {
        accessor: 'build_reference',
        title: t`Build Order`,
        sortable: false,
        render: (record: any) =>
          record.build ? (
            <Group gap={4} wrap='nowrap'>
              <Text size='sm'>{record.build_reference}</Text>
              <StatusRenderer status={record.build_status} type={ModelType.build} />
            </Group>
          ) : (
            '-'
          )
      },
      {
        accessor: 'build_title',
        title: t`Device Info`,
        sortable: false,
        render: (record: any) => record.build_title || '-'
      },
      {
        accessor: 'trip_reference',
        title: t`Trip`,
        sortable: false,
        render: (record: any) => record.trip_reference ?? '-'
      },
      {
        accessor: 'task_count',
        title: t`Tasks`,
        sortable: false
      },
      {
        accessor: 'client_name',
        title: t`Client`,
        sortable: false,
        defaultVisible: false
      },
      {
        accessor: 'risks',
        title: t`Risks`,
        sortable: false,
        render: (record: any) => <RiskBadges risks={record.risks} />
      }
    ],
    [today]
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'kind',
        label: t`Type`,
        description: t`Deployments or maintenance`,
        choices: [
          { value: 'deployment', label: t`Deployment` },
          { value: 'maintenance', label: t`Maintenance` }
        ]
      }
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      ...tripSelection.actions,
      <AddItemButton
        key='new-deployment'
        hidden={!user.hasChangeRole(UserRoles.fleet)}
        tooltip={t`Choose a device on a sales order which is not deployed, and plan where and when it goes`}
        text={t`New Deployment`}
        onClick={() => newDeployment.open()}
      />
    ],
    [user, newDeployment, tripSelection.actions]
  );

  return (
    <>
      {newDeployment.modal}
      {tripSelection.modals}
      {rowDeploymentActions.modals}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_plan)}
        tableState={table}
        columns={columns}
        props={{
          params: params,
          tableFilters: filters,
          tableActions: tableActions,
          rowActions: rowActions,
          enableSelection: true,
          isRecordSelectable: canSelect,
          enableDownload: false,
          modelType: ModelType.deployment
        }}
      />
    </>
  );
}
