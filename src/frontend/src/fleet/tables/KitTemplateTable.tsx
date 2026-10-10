import { t } from '@lingui/core/macro';
import { Badge } from '@mantine/core';
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
import { kitLineFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { PartColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';

/**
 * Kit template of a device type: the parts usually taken for a preventive
 * maintenance visit (always, or likely to be needed).
 */
export function KitTemplateTable({
  deviceTypeId,
  readOnly = false
}: Readonly<{ deviceTypeId: number; readOnly?: boolean }>) {
  const table = useTable('fleet-kit-template');
  const user = useUserState();

  const [selected, setSelected] = useState<number | undefined>(undefined);

  const canAdd = !readOnly && user.hasAddRole(UserRoles.fleet);
  const canEdit = !readOnly && user.hasChangeRole(UserRoles.fleet);
  const canDelete = !readOnly && user.hasDeleteRole(UserRoles.fleet);

  const newLine = useCreateApiFormModal({
    url: ApiEndpoints.fleet_kit_line_list,
    title: t`Add Kit Part`,
    fields: kitLineFields(deviceTypeId),
    initialData: { quantity: 1, mode: 'ALWAYS' },
    table: table
  });

  const editLine = useEditApiFormModal({
    url: ApiEndpoints.fleet_kit_line_list,
    pk: selected,
    title: t`Edit Kit Part`,
    fields: kitLineFields(deviceTypeId),
    table: table
  });

  const deleteLine = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_kit_line_list,
    pk: selected,
    title: t`Delete Kit Part`,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      PartColumn({
        part: 'part_detail',
        switchable: false
      }),
      {
        accessor: 'quantity',
        title: t`Quantity`,
        sortable: true,
        render: (record: any) => Number(record.quantity)
      },
      {
        accessor: 'mode',
        title: t`Mode`,
        sortable: true,
        render: (record: any) =>
          record.mode == 'LIKELY' ? (
            <Badge variant='light' color='yellow'>{t`Likely`}</Badge>
          ) : (
            <Badge variant='light' color='blue'>{t`Always`}</Badge>
          )
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
          editLine.open();
        }
      }),
      RowDeleteAction({
        hidden: !canDelete,
        onClick: () => {
          setSelected(record.pk);
          deleteLine.open();
        }
      })
    ],
    [canEdit, canDelete]
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-kit-line'
        hidden={!canAdd}
        tooltip={t`Add kit part`}
        onClick={() => newLine.open()}
      />
    ],
    [canAdd, newLine]
  );

  return (
    <>
      {newLine.modal}
      {editLine.modal}
      {deleteLine.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_kit_line_list)}
        tableState={table}
        columns={columns}
        props={{
          params: { device_type: deviceTypeId, part_detail: true },
          rowActions: rowActions,
          tableActions: tableActions
        }}
      />
    </>
  );
}
