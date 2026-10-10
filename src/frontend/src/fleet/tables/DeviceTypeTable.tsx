import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { useDeviceTypeFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { BooleanColumn, PartColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';

/**
 * Table of fleet device types (parts which are fleet devices)
 */
export function DeviceTypeTable() {
  const table = useTable('fleet-device-types');
  const user = useUserState();

  const fields = useDeviceTypeFields({});

  const newDeviceType = useCreateApiFormModal({
    url: ApiEndpoints.fleet_device_type_list,
    title: t`Add Device Type`,
    fields: fields,
    follow: true,
    modelType: ModelType.fleetdevicetype,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      PartColumn({ switchable: false }),
      {
        accessor: 'pm_interval_days',
        title: t`PM Interval (days)`,
        sortable: true
      },
      {
        accessor: 'stream_count',
        title: t`Streams`,
        sortable: false
      },
      {
        accessor: 'deployment_count',
        title: t`Deployed`,
        sortable: true
      },
      BooleanColumn({
        accessor: 'active',
        title: t`Active`
      })
    ],
    []
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'active',
        label: t`Active`,
        description: t`Show active device types`
      }
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-device-type'
        hidden={!user.hasAddRole(UserRoles.fleet)}
        tooltip={t`Add device type`}
        onClick={() => newDeviceType.open()}
      />
    ],
    [user, newDeviceType]
  );

  return (
    <>
      {newDeviceType.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_device_type_list)}
        tableState={table}
        columns={columns}
        props={{
          tableFilters: filters,
          tableActions: tableActions,
          modelType: ModelType.fleetdevicetype
        }}
      />
    </>
  );
}
