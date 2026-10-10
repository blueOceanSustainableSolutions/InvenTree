import { t } from '@lingui/core/macro';
import { Badge, Text, Tooltip } from '@mantine/core';
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
import { formatDate } from '../../defaults/formatters';
import { dataStreamFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { BooleanColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import {
  FleetStatus,
  FleetStatusBadge,
  fleetStatusChoices
} from '../components/FleetBadges';
import { formatAge } from '../components/StreamStatusList';

/**
 * Table of the data streams of a deployment (copied from the device type on
 * deploy). Streams can be disabled, or marked essential, per deployment.
 */
export function DataStreamTable({
  deploymentId
}: Readonly<{ deploymentId: number }>) {
  const table = useTable('fleet-data-streams');
  const user = useUserState();

  const [selected, setSelected] = useState<number | undefined>(undefined);

  const canAdd = user.hasAddRole(UserRoles.fleet);
  const canEdit = user.hasChangeRole(UserRoles.fleet);
  const canDelete = user.hasDeleteRole(UserRoles.fleet);

  const newStream = useCreateApiFormModal({
    url: ApiEndpoints.fleet_stream_list,
    title: t`Add Data Stream`,
    fields: dataStreamFields(deploymentId),
    table: table
  });

  const editFields = useMemo(() => {
    const fields = dataStreamFields(deploymentId);
    fields.key = { disabled: true };
    return fields;
  }, [deploymentId]);

  const editStream = useEditApiFormModal({
    url: ApiEndpoints.fleet_stream_list,
    pk: selected,
    title: t`Edit Data Stream`,
    fields: editFields,
    table: table
  });

  const deleteStream = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_stream_list,
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
      {
        accessor: 'state',
        title: t`State`,
        sortable: true,
        render: (record: any) =>
          record.enabled ? (
            <FleetStatusBadge type={FleetStatus.stream} status={record.state} />
          ) : (
            <Badge color='gray' variant='outline' size='sm'>
              {t`Disabled`}
            </Badge>
          )
      },
      {
        accessor: 'last_seen',
        title: t`Last Data`,
        sortable: true,
        render: (record: any) => (
          <Tooltip
            label={
              record.last_seen
                ? formatDate(record.last_seen, { showTime: true })
                : t`No data received yet`
            }
          >
            <Text size='sm'>{formatAge(record.last_seen)}</Text>
          </Tooltip>
        )
      },
      BooleanColumn({
        accessor: 'essential',
        title: t`Essential`
      }),
      BooleanColumn({
        accessor: 'enabled',
        title: t`Enabled`
      }),
      {
        accessor: 'expected_interval_minutes',
        title: t`Expected Interval (min)`,
        sortable: true
      },
      {
        accessor: 'grace_minutes',
        title: t`Grace (min)`,
        sortable: false,
        defaultVisible: false
      }
    ],
    []
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'state',
        label: t`State`,
        description: t`Filter by data stream state`,
        choiceFunction: fleetStatusChoices(FleetStatus.stream)
      },
      {
        name: 'enabled',
        label: t`Enabled`,
        description: t`Show enabled data streams`
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
    [canEdit, canDelete, editStream, deleteStream]
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
        url={apiUrl(ApiEndpoints.fleet_stream_list)}
        tableState={table}
        columns={columns}
        props={{
          params: { deployment: deploymentId },
          rowActions: rowActions,
          tableActions: tableActions,
          tableFilters: filters
        }}
      />
    </>
  );
}
