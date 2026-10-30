import { t } from '@lingui/core/macro';
import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';
import {
  IconAnchor,
  IconCalendarEvent,
  IconRoute
} from '@tabler/icons-react';
import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { AddItemButton } from '@lib/components/AddItemButton';
import type { RowAction } from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { formatDate } from '../../defaults/formatters';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { FleetStatus, FleetStatusBadge, RiskBadges } from '../components/FleetBadges';
import {
  AddToTripModal,
  useTripSelectionActions
} from '../components/TripSelection';
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

/** Badge for the carry-over of a replacement: to validate, or validated */
function CarryOverBadge({ state }: Readonly<{ state?: string }>) {
  if (state == 'TO_VALIDATE') {
    return (
      <Tooltip
        label={t`Validate the carry-over (Carry Over tab of the deployment) before adding it to a trip`}
      >
        <Badge color='orange' variant='light' size='sm'>
          {t`Carry-over to validate`}
        </Badge>
      </Tooltip>
    );
  }

  if (state == 'VALIDATED') {
    return (
      <Tooltip label={t`The parts carried over from the replaced device are validated`}>
        <Badge color='green' variant='light' size='sm'>
          {t`Carry-over validated`}
        </Badge>
      </Tooltip>
    );
  }

  return null;
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
 * "Add to Trip"); a replacement only once its carry-over is validated.
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
  const navigate = useNavigate();
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

  // "Add to Trip" on one row
  const [tripRow, setTripRow] = useState<number | null>(null);

  const rowDeploymentActions = useDeploymentActions({
    deployment: selected,
    onUpdate: () => table.refreshTable()
  });

  // A row which can be put on a trip, if not already on one: a deployed
  // device (its maintenance), or a device to deploy, built or not (a device
  // which is not built yet can only be deployed once it is ready). A
  // replacement needs its carry-over validated first.
  const canSelect = useCallback(
    (record: any): boolean =>
      !record.trip &&
      (record.kind == 'maintenance' ||
        ([
          deploymentStatus.PLANNED,
          deploymentStatus.IN_PRODUCTION,
          deploymentStatus.READY
        ].includes(record.status) &&
          record.carry_over != 'TO_VALIDATE')),
    [deploymentStatus]
  );

  const rowActions = useCallback(
    (record: any): RowAction[] => [
      {
        title: t`Add to Trip`,
        tooltip: t`Put this device on an open field trip`,
        icon: <IconRoute />,
        color: 'green',
        hidden: !canSelect(record) || !user.hasChangeRole(UserRoles.fleet),
        onClick: () => setTripRow(record.pk)
      },
      {
        title: t`Validate Carry-over`,
        tooltip: t`Check and validate the parts carried over from the replaced device, before adding it to a trip`,
        icon: <IconAnchor />,
        color: 'orange',
        hidden: record.carry_over != 'TO_VALIDATE',
        onClick: () =>
          navigate(
            `${getDetailUrl(ModelType.deployment, record.deployment)}carry-over`
          )
      },
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
      },
      {
        title: t`Change Maintenance Date`,
        tooltip: t`Change the next preventive-maintenance date`,
        icon: <IconCalendarEvent />,
        color: 'teal',
        hidden:
          record.kind != 'maintenance' ||
          !user.hasChangeRole(UserRoles.fleet),
        onClick: () => {
          setSelected(record);
          rowDeploymentActions.openChangeMaintenanceDate();
        }
      }
    ],
    [user, rowDeploymentActions, canSelect, navigate]
  );

  const today = new Date().toISOString().slice(0, 10);

  const columns: TableColumn[] = useMemo(
    () => [
      {
        accessor: 'kind',
        title: t`Type`,
        sortable: false,
        switchable: false,
        render: (record: any) => (
          <Group gap={4} wrap='nowrap'>
            <KindBadge kind={record.kind} />
            {record.kind == 'deployment' && record.manually_planned && (
              <Tooltip label={t`Planned by hand with New Deployment`}>
                <Badge color='violet' variant='filled' size='sm'>
                  {t`Manual`}
                </Badge>
              </Tooltip>
            )}
          </Group>
        )
      },
      {
        accessor: 'date',
        title: t`Date`,
        sortable: false,
        switchable: false,
        render: (record: any) =>
          record.date ? (
            <Stack gap={0}>
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
              {record.kind == 'maintenance' && record.next_pm_manual && (
                <Text size='xs' c='dimmed'>{t`Manual date`}</Text>
              )}
            </Stack>
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
              <CarryOverBadge state={record.carry_over} />
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
      },
      {
        name: 'manually_planned',
        label: t`Manually Planned`,
        description: t`Deployments planned by hand with New Deployment`
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
      <AddToTripModal
        opened={tripRow != null}
        onClose={() => setTripRow(null)}
        kind='deployments'
        ids={tripRow == null ? [] : [tripRow]}
        onAdded={() => table.refreshTable()}
      />
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
