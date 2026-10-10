import { t } from '@lingui/core/macro';
import { useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import {
  type RowAction,
  RowDeleteAction,
  RowEditAction
} from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { streamTemplateFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { BooleanColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';

/**
 * Table of the default data streams of a device type.
 * They are copied to the deployment when a device is deployed.
 */
export function StreamTemplateTable({
  deviceTypeId
}: Readonly<{ deviceTypeId: number }>) {
  const table = useTable('fleet-stream-templates');
  const user = useUserState();

  const [selected, setSelected] = useState<number | undefined>(undefined);

  const canAdd = user.hasAddRole(UserRoles.fleet);
  const canEdit = user.hasChangeRole(UserRoles.fleet);
  const canDelete = user.hasDeleteRole(UserRoles.fleet);

  const newStream = useCreateApiFormModal({
    url: ApiEndpoints.fleet_stream_template_list,
    title: t`Add Data Stream`,
    fields: streamTemplateFields(deviceTypeId),
    table: table
  });

  const editStream = useEditApiFormModal({
    url: ApiEndpoints.fleet_stream_template_list,
    pk: selected,
    title: t`Edit Data Stream`,
    fields: streamTemplateFields(deviceTypeId),
    table: table
  });

  const deleteStream = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_stream_template_list,
    pk: selected,
    title: t`Delete Data Stream`,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      {
        accessor: 'key',
        title: t`Key`,
        sortable: true,
        switchable: false
      },
      {
        accessor: 'name',
        title: t`Name`,
        sortable: true
      },
      BooleanColumn({
        accessor: 'essential',
        title: t`Essential`
      }),
      {
        accessor: 'expected_interval_minutes',
        title: t`Expected Interval (min)`,
        sortable: true
      },
      {
        accessor: 'grace_minutes',
        title: t`Grace (min)`,
        sortable: false
      }
    ],
    []
  );

  const rowActions = useCallback(
    (record: any): RowAction[] => [
      RowEditAction({
        hidden: !canEdit,
        onClick: () => {
          setSelected(record.pk);
          editStream.open();
        }
      }),
      RowDeleteAction({
        hidden: !canDelete,
        onClick: () => {
          setSelected(record.pk);
          deleteStream.open();
        }
      })
    ],
    [canEdit, canDelete]
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-stream'
        hidden={!canAdd}
        tooltip={t`Add data stream`}
        onClick={() => newStream.open()}
      />
    ],
    [canAdd, newStream]
  );

  return (
    <>
      {newStream.modal}
      {editStream.modal}
      {deleteStream.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_stream_template_list)}
        tableState={table}
        columns={columns}
        props={{
          params: { device_type: deviceTypeId },
          rowActions: rowActions,
          tableActions: tableActions
        }}
      />
    </>
  );
}
