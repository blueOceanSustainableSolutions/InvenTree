import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { useMemo } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { useUserState } from '../../states/UserState';
import {
  DateColumn,
  ReferenceColumn,
  ResponsibleColumn,
  StatusColumn
} from '../../tables/ColumnRenderers';
import { OwnerFilter, StatusFilterOptions } from '../../tables/Filter';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { userListNames } from '../components/FleetBadges';
import { useNewTripForm } from '../components/TripSelection';

/**
 * Table of fleet field trips
 */
export function TripTable({
  params = {},
  tableName
}: Readonly<{
  params?: Record<string, any>;
  tableName?: string;
}>) {
  const table = useTable(tableName ?? 'fleet-trips');
  const user = useUserState();

  const newTrip = useNewTripForm({});

  const columns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({ switchable: false }),
      {
        accessor: 'title',
        title: t`Title`,
        sortable: true
      },
      StatusColumn({ model: ModelType.fieldtrip }),
      DateColumn({
        accessor: 'start_date',
        title: t`Start Date`
      }),
      DateColumn({
        accessor: 'end_date',
        title: t`End Date`
      }),
      {
        accessor: 'vessel',
        title: t`Vessel`,
        sortable: false
      },
      ResponsibleColumn({ sortable: false, filter: 'responsible' }),
      {
        accessor: 'team',
        title: t`Team`,
        sortable: false,
        render: (record: any) => userListNames(record.team_detail)
      },
      {
        accessor: 'task_count',
        title: t`Tasks`,
        sortable: false,
        render: (record: any) => (
          <Text size='sm'>
            {record.open_task_count ?? 0} / {record.task_count ?? 0}
          </Text>
        )
      },
      {
        accessor: 'kit_line_count',
        title: t`Kit Lines`,
        sortable: false,
        defaultVisible: false
      }
    ],
    []
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'open',
        label: t`Open`,
        description: t`Show trips which are not closed or cancelled`
      },
      {
        name: 'status',
        label: t`Status`,
        description: t`Filter by trip status`,
        choiceFunction: StatusFilterOptions(ModelType.fieldtrip)
      },
      {
        name: 'mine',
        label: t`My Trips`,
        description: t`Show trips where I am in the team or responsible`
      },
      OwnerFilter({
        name: 'responsible',
        label: t`Responsible`,
        description: t`Filter by responsible owner`
      })
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='new-trip'
        hidden={!user.hasAddRole(UserRoles.fleet)}
        tooltip={t`Plan a field trip`}
        text={t`New Field Trip`}
        onClick={() => newTrip.open()}
      />
    ],
    [user, newTrip]
  );

  return (
    <>
      {newTrip.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_trip_list)}
        tableState={table}
        columns={columns}
        props={{
          params: params,
          tableFilters: filters,
          tableActions: tableActions,
          enableSelection: true,
          enableReports: true,
          enableDownload: true,
          modelType: ModelType.fieldtrip
        }}
      />
    </>
  );
}
