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
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { checklistItemFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { BooleanColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { taskTypeChoices, taskTypeLabel } from '../components/FleetBadges';

/** Label of a checklist item kind */
function kindLabel(kind: string): string {
  switch (kind) {
    case 'MEASUREMENT':
      return t`Measurement`;
    case 'PHOTO':
      return t`Photo`;
    default:
      return t`Check`;
  }
}

/**
 * Checklist template of a device type.
 * A task gets the items of its task type (and those for every type) when it starts.
 */
export function ChecklistTemplateTable({
  deviceTypeId
}: Readonly<{ deviceTypeId: number }>) {
  const table = useTable('fleet-checklist-template');
  const user = useUserState();

  const [selected, setSelected] = useState<number | undefined>(undefined);

  const canAdd = user.hasAddRole(UserRoles.fleet);
  const canEdit = user.hasChangeRole(UserRoles.fleet);
  const canDelete = user.hasDeleteRole(UserRoles.fleet);

  const newItem = useCreateApiFormModal({
    url: ApiEndpoints.fleet_checklist_item_list,
    title: t`Add Checklist Item`,
    fields: checklistItemFields(deviceTypeId),
    initialData: { task_type: 'PREVENTIVE', required: true },
    table: table
  });

  const editItem = useEditApiFormModal({
    url: ApiEndpoints.fleet_checklist_item_list,
    pk: selected,
    title: t`Edit Checklist Item`,
    fields: checklistItemFields(deviceTypeId),
    table: table
  });

  const deleteItem = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_checklist_item_list,
    pk: selected,
    title: t`Delete Checklist Item`,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      {
        accessor: 'sequence',
        title: t`Order`,
        sortable: true,
        switchable: false
      },
      {
        accessor: 'text',
        title: t`Check`,
        sortable: true,
        switchable: false
      },
      {
        accessor: 'kind',
        title: t`Kind`,
        sortable: true,
        render: (record: any) =>
          record.kind == 'MEASUREMENT' && record.unit
            ? `${kindLabel(record.kind)} (${record.unit})`
            : kindLabel(record.kind)
      },
      {
        accessor: 'task_type',
        title: t`Task Type`,
        sortable: true,
        render: (record: any) =>
          record.task_type ? (
            taskTypeLabel(record.task_type)
          ) : (
            <Badge variant='light' color='gray'>{t`Every task`}</Badge>
          )
      },
      BooleanColumn({
        accessor: 'required',
        title: t`Required`
      })
    ],
    []
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'task_type',
        label: t`Task Type`,
        description: t`Filter by task type`,
        choices: taskTypeChoices()
      },
      {
        name: 'required',
        label: t`Required`,
        description: t`Show required items`
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
          editItem.open();
        }
      }),
      RowDeleteAction({
        hidden: !canDelete,
        onClick: () => {
          setSelected(record.pk);
          deleteItem.open();
        }
      })
    ],
    [canEdit, canDelete]
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-checklist-item'
        hidden={!canAdd}
        tooltip={t`Add checklist item`}
        onClick={() => newItem.open()}
      />
    ],
    [canAdd, newItem]
  );

  return (
    <>
      {newItem.modal}
      {editItem.modal}
      {deleteItem.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_checklist_item_list)}
        tableState={table}
        columns={columns}
        props={{
          params: { device_type: deviceTypeId },
          tableFilters: filters,
          rowActions: rowActions,
          tableActions: tableActions
        }}
      />
    </>
  );
}
