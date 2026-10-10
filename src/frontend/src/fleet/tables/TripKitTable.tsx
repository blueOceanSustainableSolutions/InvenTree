import { t } from '@lingui/core/macro';
import { Badge, Button, Text } from '@mantine/core';
import { IconHandGrab, IconPackageImport, IconWand } from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import {
  type RowAction,
  RowDeleteAction,
  RowEditAction
} from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import {
  type PrepareKitRowItem,
  prepareKitData,
  prepareKitFields,
  tripKitLineFields
} from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { PartColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';

/** Label of the source of a trip kit line */
export function kitSourceLabel(source: string): string {
  switch (source) {
    case 'ALWAYS':
      return t`Always`;
    case 'LIKELY':
      return t`Likely`;
    case 'ALERT':
      return t`Alert`;
    case 'MANUAL':
      return t`Manual`;
    case 'DEVICE':
      return t`Device`;
    default:
      return source;
  }
}

/** Badge colour of the source of a trip kit line */
function kitSourceColor(source: string): string {
  switch (source) {
    case 'ALWAYS':
      return 'blue';
    case 'LIKELY':
      return 'cyan';
    case 'ALERT':
      return 'orange';
    case 'DEVICE':
      return 'green';
    default:
      return 'gray';
  }
}

/** A prepare kit form row for a kit line: its part, or its device */
function kitLineRow(line: any): PrepareKitRowItem {
  return {
    part: line.part,
    part_detail: line.part_detail,
    missing: line.missing,
    fixed: !!line.stock_item,
    stock_item: line.stock_item ?? undefined,
    quantity: line.stock_item ? undefined : line.missing
  };
}

/**
 * Form to take stock into the kit of a trip: one row per stock item, as the
 * core allocation forms.
 *
 * open() takes the kit lines to pre-fill (one row each, filtered by the part
 * of the line; a DEVICE line fixes its device); without lines, the form
 * starts with one empty row. Rows can be added and removed in the form.
 */
export function usePrepareKitForm({
  tripId,
  onSuccess
}: {
  tripId?: number;
  onSuccess: () => void;
}) {
  const [rows, setRows] = useState<PrepareKitRowItem[]>([]);

  const fields = useMemo(() => prepareKitFields(), []);

  const form = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_prepare_kit, tripId),
    title: t`Take Stock into Kit`,
    fields: fields,
    initialData: {
      items: rows
    },
    preFormContent: (
      <Text size='sm' c='dimmed'>
        {t`The stock is moved into the kit location of the trip. A trip in planning becomes "Kit ready". Leave the quantity blank to take the whole stock item.`}
      </Text>
    ),
    processFormData: prepareKitData,
    successMessage: t`Stock moved into the kit`,
    onFormSuccess: onSuccess,
    size: '80%'
  });

  const open = useCallback(
    (lines?: any[]) => {
      setRows(
        lines && lines.length > 0
          ? lines.map(kitLineRow)
          : [{ stock_item: undefined, quantity: undefined }]
      );
      form.open();
    },
    [form]
  );

  return { modal: form.modal, open };
}

/**
 * The planned kit of a trip (from `trip/<pk>/kit/`): each line with what is
 * available in stock, what is already in the kit and what is missing.
 *
 * Suggested lines (always / likely / alert / device) are regenerated with
 * "Suggest Kit"; lines added by hand are MANUAL and can be edited.
 */
export function TripKitTable({
  trip,
  lines,
  editable,
  canTake,
  onChange
}: Readonly<{
  trip: any;
  lines: any[];
  editable: boolean;
  canTake: boolean;
  onChange: () => void;
}>): ReactNode {
  const table = useTable('fleet-trip-kit');
  const user = useUserState();

  const [selected, setSelected] = useState<any>({});

  const canAdd = editable && user.hasAddRole(UserRoles.fleet);
  const canEdit = editable && user.hasChangeRole(UserRoles.fleet);
  const canDelete = editable && user.hasDeleteRole(UserRoles.fleet);

  const records = useMemo(
    () =>
      (lines ?? []).map((entry: any) => ({
        ...entry.line,
        available: entry.available,
        in_kit: entry.in_kit,
        missing: entry.missing
      })),
    [lines]
  );

  const suggestKit = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_suggest_kit, trip.pk),
    title: t`Suggest Kit`,
    preFormContent: (
      <Text size='sm'>
        {t`Rebuild the suggested lines from the kit templates of the device types, the open alerts and the devices to deploy. Lines added by hand are kept.`}
      </Text>
    ),
    successMessage: t`Kit suggested`,
    onFormSuccess: onChange
  });

  const newLine = useCreateApiFormModal({
    url: ApiEndpoints.fleet_trip_kit_line_list,
    title: t`Add Kit Part`,
    fields: tripKitLineFields(trip.pk),
    initialData: { quantity_planned: 1 },
    onFormSuccess: onChange
  });

  const editLine = useEditApiFormModal({
    url: ApiEndpoints.fleet_trip_kit_line_list,
    pk: selected.pk,
    title: t`Edit Kit Part`,
    fields: tripKitLineFields(trip.pk),
    onFormSuccess: onChange
  });

  const deleteLine = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_trip_kit_line_list,
    pk: selected.pk,
    title: t`Delete Kit Part`,
    onFormSuccess: onChange
  });

  const prepareKit = usePrepareKitForm({
    tripId: trip.pk,
    onSuccess: () => {
      table.clearSelectedRecords();
      onChange();
    }
  });

  // Kit lines which still miss stock can be ticked and taken together
  const canTakeLine = useCallback(
    (record: any): boolean => canTake && record.missing > 0,
    [canTake]
  );

  const columns: TableColumn[] = useMemo(
    () => [
      PartColumn({
        part: 'part_detail',
        switchable: false
      }),
      {
        accessor: 'source',
        title: t`Source`,
        sortable: false,
        render: (record: any) => (
          <Badge color={kitSourceColor(record.source)} variant='light'>
            {kitSourceLabel(record.source)}
          </Badge>
        )
      },
      {
        accessor: 'serial',
        title: t`Device / Task`,
        sortable: false,
        render: (record: any) =>
          [record.serial ? `#${record.serial}` : null, record.task_reference]
            .filter(Boolean)
            .join(' · ') || '-'
      },
      {
        accessor: 'quantity_planned',
        title: t`Planned`,
        sortable: false,
        render: (record: any) => Number(record.quantity_planned)
      },
      {
        accessor: 'available',
        title: t`Available`,
        sortable: false,
        render: (record: any) => (
          <Text
            size='sm'
            c={record.available < record.missing ? 'red' : undefined}
          >
            {record.available}
          </Text>
        )
      },
      {
        accessor: 'in_kit',
        title: t`In Kit`,
        sortable: false
      },
      {
        accessor: 'missing',
        title: t`Missing`,
        sortable: false,
        render: (record: any) =>
          record.missing > 0 ? (
            <Badge color='orange' variant='light'>
              {record.missing}
            </Badge>
          ) : (
            <Badge color='green' variant='light'>{t`Complete`}</Badge>
          )
      },
      {
        accessor: 'note',
        title: t`Note`,
        sortable: false
      }
    ],
    []
  );

  const rowActions = useCallback(
    (record: any): RowAction[] => {
      const manual = record.source == 'MANUAL';

      return [
        {
          title: t`Take`,
          tooltip: t`Move stock for this line into the kit`,
          icon: <IconHandGrab />,
          color: 'green',
          hidden: !canTake || record.missing <= 0,
          onClick: () => prepareKit.open([record])
        },
        RowEditAction({
          hidden: !canEdit || !manual,
          onClick: () => {
            setSelected(record);
            editLine.open();
          }
        }),
        RowDeleteAction({
          hidden: !canDelete || !manual,
          onClick: () => {
            setSelected(record);
            deleteLine.open();
          }
        })
      ];
    },
    [canTake, canEdit, canDelete, prepareKit, editLine, deleteLine]
  );

  const tableActions = useMemo(
    () => [
      editable && user.hasChangeRole(UserRoles.fleet) ? (
        <Button
          key='suggest-kit'
          variant='outline'
          leftSection={<IconWand size={18} />}
          onClick={() => suggestKit.open()}
        >
          {t`Suggest Kit`}
        </Button>
      ) : null,
      canTake ? (
        <Button
          key='take-stock'
          variant='outline'
          leftSection={<IconPackageImport size={18} />}
          onClick={() => prepareKit.open(table.selectedRecords)}
        >
          {table.selectedRecords.length > 0 ? t`Take Selected` : t`Take Stock`}
        </Button>
      ) : null,
      <AddItemButton
        key='add-line'
        hidden={!canAdd}
        tooltip={t`Add a part to the kit`}
        text={t`Add Part`}
        onClick={() => newLine.open()}
      />
    ],
    [editable, canTake, canAdd, user, suggestKit, prepareKit, newLine, table]
  );

  return (
    <>
      {suggestKit.modal}
      {newLine.modal}
      {editLine.modal}
      {deleteLine.modal}
      {prepareKit.modal}
      <InvenTreeTable
        tableData={records}
        tableState={table}
        columns={columns}
        props={{
          rowActions: rowActions,
          tableActions: tableActions,
          enableSelection: canTake,
          isRecordSelectable: canTakeLine,
          enableSearch: false,
          enableFilters: false,
          enableRefresh: false,
          enablePagination: false,
          noRecordsText: t`No kit lines: use "Suggest Kit" or add parts`
        }}
      />
    </>
  );
}

/**
 * Stock in a trip kit, or in its "Removed" location (from `trip/<pk>/kit/`)
 */
export function KitStockTable({
  items,
  tableName
}: Readonly<{
  items: any[];
  tableName: string;
}>): ReactNode {
  const table = useTable(tableName);

  const columns: TableColumn[] = useMemo(
    () => [
      {
        accessor: 'part_name',
        title: t`Part`,
        sortable: false,
        switchable: false
      },
      {
        accessor: 'serial',
        title: t`Stock`,
        sortable: false,
        render: (record: any) =>
          record.serial
            ? `#${record.serial}`
            : [
                record.quantity,
                record.batch ? `(${t`Batch`} ${record.batch})` : null
              ]
                .filter((value) => value != null)
                .join(' ')
      },
      {
        accessor: 'status_text',
        title: t`Status`,
        sortable: false
      },
      {
        accessor: 'location_name',
        title: t`Location`,
        sortable: false
      }
    ],
    []
  );

  return (
    <InvenTreeTable
      tableData={items ?? []}
      tableState={table}
      columns={columns}
      props={{
        enableSearch: false,
        enableFilters: false,
        enableRefresh: false,
        enablePagination: false,
        enableColumnSwitching: false,
        modelType: ModelType.stockitem,
        minHeight: 100
      }}
    />
  );
}
