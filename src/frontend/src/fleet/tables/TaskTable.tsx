import { t } from '@lingui/core/macro';
import { Group, Stack, Text, Tooltip } from '@mantine/core';
import { IconCalendarExclamation, IconRouteOff } from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import type { RowAction } from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn, TableState } from '@lib/types/Tables';
import { formatDate } from '../../defaults/formatters';
import { useTaskFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import {
  DateColumn,
  DescriptionColumn,
  ReferenceColumn,
  StatusColumn,
  UserColumn
} from '../../tables/ColumnRenderers';
import {
  AssignedToMeFilter,
  OverdueFilter,
  StatusFilterOptions
} from '../../tables/Filter';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import {
  taskTypeChoices,
  taskTypeLabel,
  userListNames
} from '../components/FleetBadges';
import { useTripSelectionActions } from '../components/TripSelection';

/**
 * Table of fleet maintenance tasks.
 *
 * Each task is shown by its site nickname, or by the device serial when the
 * device has no site.
 *
 * - selection 'trip': plan selection, the open tasks without a trip can be
 *   ticked and grouped with "Create Field Trip" / "Add to Trip";
 * - selection 'picker': the same checkboxes, read by the caller through
 *   `tableState` (e.g. the "Add tasks" picker of a trip);
 * - tripId: the tasks of a trip, with the row action "Remove from Trip".
 */
export function TaskTable({
  params = {},
  tableName,
  device,
  deployment,
  selection,
  tableState,
  tripId,
  tripEditable = false,
  extraActions = [],
  onTripChange
}: Readonly<{
  params?: Record<string, any>;
  tableName?: string;
  device?: number;
  deployment?: number;
  selection?: 'trip' | 'picker';
  tableState?: TableState;
  tripId?: number;
  tripEditable?: boolean;
  extraActions?: ReactNode[];
  onTripChange?: () => void;
}>) {
  const ownTable = useTable(tableName ?? 'fleet-tasks');
  const table = tableState ?? ownTable;
  const user = useUserState();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });

  // Proposed or scheduled: the task is not started yet
  const notStarted = useCallback(
    (record: any): boolean =>
      record.status == taskStatus.PROPOSED ||
      record.status == taskStatus.SCHEDULED,
    [taskStatus]
  );

  // A task which can still be put on a trip: not started, no trip
  const canPlanTask = useCallback(
    (record: any): boolean => notStarted(record) && !record.trip,
    [notStarted]
  );

  const [selectedTask, setSelectedTask] = useState<any>({});

  const tripSelection = useTripSelectionActions({
    table: table,
    kind: 'tasks',
    enabled: selection == 'trip'
  });

  const removeFromTrip = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_remove_task, tripId),
    title: t`Remove from Trip`,
    preFormWarning: t`The task goes back to proposed and keeps its due date.`,
    preFormContent: (
      <Text size='sm'>
        {selectedTask.reference} {selectedTask.display_name}
      </Text>
    ),
    processFormData: () => ({ task: selectedTask.pk }),
    successMessage: t`Task removed from the trip`,
    onFormSuccess: () => {
      table.refreshTable();
      onTripChange?.();
    }
  });

  const rowActions = useCallback(
    (record: any): RowAction[] => [
      {
        title: t`Remove from Trip`,
        tooltip: t`Take this task off the trip`,
        icon: <IconRouteOff />,
        color: 'red',
        hidden:
          !tripId ||
          !tripEditable ||
          !user.hasChangeRole(UserRoles.fleet) ||
          !notStarted(record),
        onClick: () => {
          setSelectedTask(record);
          removeFromTrip.open();
        }
      }
    ],
    [tripId, tripEditable, user, removeFromTrip, notStarted]
  );

  const createFields = useTaskFields({
    create: true,
    deviceId: device,
    deploymentId: deployment
  });

  const newTask = useCreateApiFormModal({
    url: ApiEndpoints.fleet_task_list,
    title: t`New Maintenance Task`,
    fields: createFields,
    initialData: {
      task_type: 'CORRECTIVE',
      device: device,
      deployment: deployment
    },
    follow: true,
    modelType: ModelType.maintenancetask,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({ switchable: false }),
      StatusColumn({ model: ModelType.maintenancetask }),
      {
        accessor: 'task_type',
        title: t`Type`,
        sortable: true,
        render: (record: any) => taskTypeLabel(record.task_type)
      },
      {
        accessor: 'site',
        title: t`Site / Device`,
        sortable: true,
        render: (record: any) => (
          <Stack gap={0}>
            <Text size='sm'>{record.display_name}</Text>
            {record.site_detail && record.device_detail && (
              <Text size='xs' c='dimmed'>
                #{record.device_detail.serial}
              </Text>
            )}
          </Stack>
        )
      },
      DescriptionColumn({}),
      {
        accessor: 'deployment',
        title: t`Deployment`,
        sortable: true,
        hidden: !!params.deployment,
        defaultVisible: false,
        render: (record: any) => record.deployment_detail?.reference ?? '-'
      },
      {
        accessor: 'due_date',
        title: t`Due Date`,
        sortable: true,
        render: (record: any) =>
          record.due_date ? (
            <Group gap={4} wrap='nowrap'>
              <Text size='sm' c={record.overdue ? 'red' : undefined}>
                {formatDate(record.due_date)}
              </Text>
              {record.overdue && (
                <Tooltip label={t`Overdue`}>
                  <IconCalendarExclamation size={16} color='red' />
                </Tooltip>
              )}
            </Group>
          ) : (
            '-'
          )
      },
      DateColumn({
        accessor: 'scheduled_date',
        title: t`Scheduled`
      }),
      {
        accessor: 'trip_reference',
        title: t`Field Trip`,
        sortable: false,
        hidden: !!params.trip,
        defaultVisible: false
      },
      {
        accessor: 'technicians',
        title: t`Technicians`,
        sortable: false,
        defaultVisible: false,
        render: (record: any) => userListNames(record.technicians_detail)
      },
      {
        accessor: 'action_count',
        title: t`Actions`,
        sortable: false,
        defaultVisible: false
      },
      DateColumn({
        accessor: 'completed_at',
        title: t`Completed`
      }),
      UserColumn({
        accessor: 'completed_by_detail',
        title: t`Completed By`,
        sortable: false,
        defaultVisible: false
      })
    ],
    [params]
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'open',
        label: t`Open`,
        description: t`Show tasks which are proposed, scheduled or in progress`
      },
      {
        name: 'status',
        label: t`Status`,
        description: t`Filter by task status`,
        choiceFunction: StatusFilterOptions(ModelType.maintenancetask)
      },
      {
        name: 'task_type',
        label: t`Type`,
        description: t`Filter by task type`,
        choices: taskTypeChoices()
      },
      {
        ...OverdueFilter(),
        description: t`Show open tasks whose due date has passed`
      },
      {
        ...AssignedToMeFilter(),
        description: t`Show the tasks I work on, or which are on my trips`
      },
      {
        name: 'has_trip',
        label: t`Has Trip`,
        description: t`Show tasks which are on a field trip`
      }
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      ...tripSelection.actions,
      ...extraActions,
      <AddItemButton
        key='new-task'
        hidden={
          !user.hasAddRole(UserRoles.fleet) || !!tripId || selection == 'picker'
        }
        tooltip={t`Create a maintenance task`}
        text={t`New Task`}
        onClick={() => newTask.open()}
      />
    ],
    [user, newTask, tripId, selection, tripSelection.actions, extraActions]
  );

  return (
    <>
      {newTask.modal}
      {tripSelection.modals}
      {removeFromTrip.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_task_list)}
        tableState={table}
        columns={columns}
        props={{
          params: params,
          tableFilters: filters,
          tableActions: tableActions,
          rowActions: tripId ? rowActions : undefined,
          enableSelection: true,
          isRecordSelectable: selection ? canPlanTask : undefined,
          enableReports: selection != 'picker',
          enableDownload: true,
          modelType: ModelType.maintenancetask
        }}
      />
    </>
  );
}
