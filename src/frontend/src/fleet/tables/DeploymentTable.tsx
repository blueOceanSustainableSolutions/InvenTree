import { t } from '@lingui/core/macro';
import { Badge, Stack, Text } from '@mantine/core';
import {
  IconCalendarEvent,
  IconHammer,
  IconPackageImport
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
import type { TableColumn, TableState } from '@lib/types/Tables';
import { RenderInlineModel } from '../../components/render/Instance';
import { useDeploymentFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import {
  DateColumn,
  ReferenceColumn,
  StatusColumn,
  TargetDateColumn
} from '../../tables/ColumnRenderers';
import { StatusFilterOptions } from '../../tables/Filter';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import {
  CoverageBadge,
  DeviceStateBadge,
  FleetStatus,
  FleetStatusBadge,
  RiskBadges,
  deviceStateChoices,
  fleetStatusChoices
} from '../components/FleetBadges';
import { formatAge } from '../components/StreamStatusList';
import { useTripSelectionActions } from '../components/TripSelection';
import { useDeploymentActions } from '../hooks/DeploymentActions';

export type DeploymentTableMode = 'pipeline' | 'active' | 'all';

/** Choices for the coverage filter */
function coverageChoices() {
  return [
    { value: 'FULL', label: t`Full service` },
    { value: 'NO_SERVICE', label: t`Monitored only` },
    { value: 'THIRD_PARTY', label: t`Third party` }
  ];
}

/** Whole days since a deployed device went into the water */
function daysDeployed(record: any, deployed: number): string | number {
  if (record.status != deployed || !record.deployed_at) {
    return '-';
  }

  return Math.max(
    0,
    Math.floor((Date.now() - new Date(record.deployed_at).getTime()) / 86400000)
  );
}

/**
 * Table of fleet deployments.
 *
 * - pipeline: planned to scheduled (with readiness risks)
 * - active: deployed devices
 * - all: every deployment (e.g. the history of a site)
 *
 * selection 'trip' (plan selection) lets the ready deployments be ticked and
 * grouped with "Create Field Trip" / "Add to Trip"; 'picker' only shows the
 * checkboxes, read by the caller through `tableState`.
 */
export function DeploymentTable({
  mode = 'all',
  params = {},
  tableName,
  selection,
  tableState
}: Readonly<{
  mode?: DeploymentTableMode;
  params?: Record<string, any>;
  tableName?: string;
  selection?: 'trip' | 'picker';
  tableState?: TableState;
}>) {
  const ownTable = useTable(tableName ?? `fleet-deployments-${mode}`);
  const table = tableState ?? ownTable;
  const user = useUserState();
  const navigate = useNavigate();

  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });
  const buildStatus = useStatusCodes({ modelType: ModelType.build });

  // A deployment which can be put on a field trip: ready (built)
  const canPlanDeployment = useCallback(
    (record: any): boolean => record.status == deploymentStatus.READY,
    [deploymentStatus]
  );

  const tripSelection = useTripSelectionActions({
    table: table,
    kind: 'deployments',
    enabled: selection == 'trip'
  });

  // Pipeline row actions: plan (site and date), create build order, assign a unit
  const [selected, setSelected] = useState<any>({});

  const rowDeploymentActions = useDeploymentActions({
    deployment: selected,
    onUpdate: () => table.refreshTable()
  });

  const rowActions = useCallback(
    (record: any): RowAction[] => {
      if (mode != 'pipeline' || selection == 'picker') {
        return [];
      }

      const run = (action: () => void) => {
        setSelected(record);
        action();
      };

      return [
        {
          title: t`Set Site and Date`,
          tooltip: t`Where and when the device is deployed`,
          icon: <IconCalendarEvent />,
          color: 'blue',
          hidden: !user.hasChangeRole(UserRoles.fleet),
          onClick: () => run(rowDeploymentActions.openPlan)
        },
        {
          title: t`Create Build Order`,
          icon: <IconHammer />,
          color: 'blue',
          hidden:
            record.status != deploymentStatus.PLANNED ||
            !!record.build ||
            !!record.device ||
            !user.hasAddRole(UserRoles.fleet) ||
            !user.hasAddRole(UserRoles.build),
          onClick: () => run(rowDeploymentActions.openCreateBuild)
        },
        {
          title: t`Assign Device`,
          tooltip: t`Use an existing unit from stock`,
          icon: <IconPackageImport />,
          color: 'cyan',
          hidden:
            !!record.device ||
            // Build order still open: pending, in production or on hold
            [
              buildStatus.PENDING,
              buildStatus.PRODUCTION,
              buildStatus.ON_HOLD
            ].includes(record.build_detail?.status) ||
            !user.hasChangeRole(UserRoles.fleet),
          onClick: () => run(rowDeploymentActions.openAssignDevice)
        }
      ];
    },
    [mode, selection, user, rowDeploymentActions, deploymentStatus, buildStatus]
  );

  const createFields = useDeploymentFields({ create: true });

  const planDeployment = useCreateApiFormModal({
    url: ApiEndpoints.fleet_deployment_list,
    title: t`Plan Deployment`,
    fields: createFields,
    initialData: {
      site: params.site,
      device_type: params.device_type
    },
    follow: true,
    modelType: ModelType.deployment,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({ switchable: false }),
      StatusColumn({ model: ModelType.deployment }),
      {
        accessor: 'device_state',
        title: t`State`,
        sortable: true,
        hidden: mode == 'pipeline',
        render: (record: any) => (
          <DeviceStateBadge
            state={record.device_state}
            note={record.internal_state_note}
          />
        )
      },
      {
        accessor: 'site',
        title: t`Site`,
        sortable: true,
        render: (record: any) =>
          record.site_detail ? (
            <Stack gap={0}>
              <Text size='sm'>{record.site_detail.name}</Text>
              <Text size='xs' c='dimmed'>
                {record.site_detail.reference}
              </Text>
            </Stack>
          ) : (
            <Text size='sm' c='dimmed' fs='italic'>{t`No site`}</Text>
          )
      },
      {
        accessor: 'device',
        title: t`Device`,
        sortable: true,
        render: (record: any) =>
          record.device_detail ? (
            <Stack gap={0}>
              <Text size='sm'>#{record.device_detail.serial}</Text>
              <Text size='xs' c='dimmed'>
                {record.device_detail.part_name}
              </Text>
            </Stack>
          ) : (
            <Text size='sm' c='dimmed'>
              {record.device_type_detail?.part_name}
            </Text>
          )
      },
      {
        accessor: 'client',
        title: t`Client`,
        sortable: false,
        defaultVisible: mode != 'pipeline',
        render: (record: any) => record.client_detail?.name ?? '-'
      },
      {
        accessor: 'latitude',
        title: t`Position`,
        sortable: false,
        hidden: mode == 'pipeline',
        render: (record: any) =>
          record.latitude != null && record.longitude != null ? (
            <Text size='sm'>
              {Number(record.latitude).toFixed(4)},{' '}
              {Number(record.longitude).toFixed(4)}
            </Text>
          ) : (
            <Badge color='orange' variant='light'>{t`Not set`}</Badge>
          )
      },
      {
        accessor: 'build',
        title: t`Build Order`,
        sortable: true,
        hidden: mode == 'active',
        render: (record: any) =>
          record.build ? (
            <RenderInlineModel
              primary={record.build_detail?.reference ?? `#${record.build}`}
              url={getDetailUrl(ModelType.build, record.build)}
              navigate={navigate}
            />
          ) : (
            '-'
          )
      },
      TargetDateColumn({ hidden: mode == 'active' }),
      {
        accessor: 'risks',
        title: t`Risks`,
        sortable: false,
        hidden: mode != 'pipeline',
        render: (record: any) => <RiskBadges risks={record.risks} />
      },
      DateColumn({
        accessor: 'deployed_at',
        title: t`Deployed`,
        hidden: mode == 'pipeline'
      }),
      {
        accessor: 'health',
        title: t`Health`,
        sortable: true,
        hidden: mode == 'pipeline',
        render: (record: any) =>
          record.status == deploymentStatus.DEPLOYED ? (
            <FleetStatusBadge
              type={FleetStatus.health}
              status={record.health}
            />
          ) : (
            '-'
          )
      },
      {
        accessor: 'last_contact',
        title: t`Last Data`,
        sortable: true,
        hidden: mode == 'pipeline',
        render: (record: any) =>
          record.status == deploymentStatus.DEPLOYED
            ? formatAge(record.last_contact)
            : '-'
      },
      {
        accessor: 'days_deployed',
        title: t`Days Deployed`,
        sortable: false,
        defaultVisible: mode == 'active',
        hidden: mode == 'pipeline',
        render: (record: any) => daysDeployed(record, deploymentStatus.DEPLOYED)
      },
      DateColumn({
        accessor: 'next_pm_date',
        title: t`Next PM`,
        hidden: mode == 'pipeline'
      }),
      {
        accessor: 'coverage',
        title: t`Coverage`,
        sortable: true,
        render: (record: any) => <CoverageBadge coverage={record.coverage} />
      },
      {
        accessor: 'platform_id',
        title: t`Platform ID`,
        sortable: false,
        defaultVisible: false
      }
    ],
    [mode, navigate, deploymentStatus]
  );

  const filters: TableFilter[] = useMemo(() => {
    const result: TableFilter[] = [
      {
        name: 'status',
        label: t`Status`,
        description: t`Filter by deployment status`,
        choiceFunction: StatusFilterOptions(ModelType.deployment)
      },
      {
        name: 'coverage',
        label: t`Coverage`,
        description: t`Filter by service coverage`,
        choices: coverageChoices()
      }
    ];

    result.push(
      {
        name: 'has_site',
        label: t`Has Site`,
        description: t`Show deployments with a site`
      },
      {
        name: 'has_position',
        label: t`Has Position`,
        description: t`Show deployments with a position set`
      }
    );

    if (mode != 'pipeline') {
      result.push(
        {
          name: 'client',
          label: t`Client`,
          description: t`Filter by client`,
          type: 'api',
          apiUrl: apiUrl(ApiEndpoints.company_list),
          apiFilter: { is_customer: true },
          model: ModelType.company,
          modelRenderer: (instance: any) => instance.name
        },
        {
          name: 'device_state',
          label: t`Device State`,
          description: t`Filter by the internal state of the device`,
          choices: deviceStateChoices()
        },
        {
          name: 'health',
          label: t`Health`,
          description: t`Filter by device health`,
          choiceFunction: fleetStatusChoices(FleetStatus.health)
        },
        {
          name: 'pm_due_before',
          label: t`PM Due Before`,
          description: t`Show deployments whose next PM is due before this date`,
          type: 'date'
        },
        {
          name: 'pm_overdue',
          label: t`PM Overdue`,
          description: t`Show deployed devices whose PM date has passed`
        }
      );
    }

    if (mode != 'active') {
      result.push(
        {
          name: 'unscheduled',
          label: t`Unscheduled`,
          description: t`Show deployments without a target date`
        },
        {
          name: 'has_device',
          label: t`Has Device`,
          description: t`Show deployments with a device assigned`
        }
      );
    }

    if (mode == 'all') {
      result.push(
        {
          name: 'pipeline',
          label: t`Pipeline`,
          description: t`Show deployments which are not yet deployed`
        },
        {
          name: 'active',
          label: t`Deployed`,
          description: t`Show deployments which are deployed now`
        },
        {
          name: 'decommissioned',
          label: t`Decommissioned`,
          description: t`Show deployments of decommissioned devices`
        }
      );
    }

    return result;
  }, [mode]);

  const tableActions = useMemo(
    () => [
      ...tripSelection.actions,
      <AddItemButton
        key='plan-deployment'
        hidden={
          mode == 'active' ||
          selection == 'picker' ||
          !user.hasAddRole(UserRoles.fleet)
        }
        tooltip={t`Plan a deployment`}
        text={t`Plan Deployment`}
        onClick={() => planDeployment.open()}
      />
    ],
    [mode, user, planDeployment, selection, tripSelection.actions]
  );

  const queryParams = useMemo(() => {
    const result: Record<string, any> = { ...params, client_detail: true };

    if (mode == 'pipeline') {
      result.pipeline = true;
      result.risks = true;
    } else if (mode == 'active') {
      result.active = true;
    }

    // The build order column (linked by its reference)
    if (mode != 'active') {
      result.build_detail = true;
    }

    return result;
  }, [mode, params]);

  return (
    <>
      {planDeployment.modal}
      {tripSelection.modals}
      {rowDeploymentActions.modals}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_deployment_list)}
        tableState={table}
        columns={columns}
        props={{
          params: queryParams,
          tableFilters: filters,
          tableActions: tableActions,
          rowActions: rowActions,
          enableSelection: true,
          isRecordSelectable: selection ? canPlanDeployment : undefined,
          enableReports: selection != 'picker',
          enableDownload: true,
          modelType: ModelType.deployment
        }}
      />
    </>
  );
}
