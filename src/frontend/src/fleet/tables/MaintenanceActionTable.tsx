import { t } from '@lingui/core/macro';
import { Badge, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { DateColumn, UserColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { maintenanceActionLabel } from '../components/FleetBadges';

/** Short description of a component (part and serial) */
function ComponentCell({ item }: Readonly<{ item: any }>) {
  if (!item) {
    return '-';
  }

  return (
    <Stack gap={0}>
      <Text size='sm'>{item.part_name}</Text>
      {item.serial && (
        <Text size='xs' c='dimmed'>
          #{item.serial}
        </Text>
      )}
    </Stack>
  );
}

/**
 * The actions recorded during a maintenance task (component changes,
 * consumables, repairs, ...), oldest first.
 */
export function MaintenanceActionTable({
  taskId,
  tableName
}: Readonly<{ taskId: number; tableName?: string }>) {
  const table = useTable(tableName ?? 'fleet-task-actions');

  const columns: TableColumn[] = useMemo(
    () => [
      DateColumn({
        accessor: 'created_at',
        title: t`Time`,
        extra: { showTime: true }
      }),
      {
        accessor: 'action',
        title: t`Action`,
        sortable: true,
        switchable: false,
        render: (record: any) => (
          <Badge variant='light'>{maintenanceActionLabel(record.action)}</Badge>
        )
      },
      {
        accessor: 'component_out',
        title: t`Removed`,
        sortable: false,
        render: (record: any) =>
          record.action == 'CONSUME' ? (
            '-'
          ) : (
            <Stack gap={0}>
              <ComponentCell item={record.component_out_detail} />
              {record.disposition && record.disposition != 'keep' && (
                <Text size='xs' c='red'>
                  {record.disposition}
                </Text>
              )}
            </Stack>
          )
      },
      {
        accessor: 'component_in',
        title: t`Installed / Used`,
        sortable: false,
        render: (record: any) =>
          record.action == 'CONSUME' ? (
            <Text size='sm'>
              {Number(record.quantity)} x {record.part_name}
            </Text>
          ) : (
            <ComponentCell item={record.component_in_detail} />
          )
      },
      {
        accessor: 'destination_name',
        title: t`Destination`,
        sortable: false,
        defaultVisible: false
      },
      {
        accessor: 'fault_code',
        title: t`Fault`,
        sortable: false,
        render: (record: any) => record.fault_code_detail?.name ?? '-'
      },
      {
        accessor: 'note',
        title: t`Note`,
        sortable: false,
        render: (record: any) => {
          const extra: string[] = [];

          if (record.metadata?.disabled_stream) {
            extra.push(
              `${t`Stream disabled`}: ${record.metadata.disabled_stream}`
            );
          }

          if (record.metadata?.new_firmware) {
            extra.push(`${t`Firmware`}: ${record.metadata.new_firmware}`);
          }

          if (record.metadata?.new_position) {
            const pos = record.metadata.new_position;
            extra.push(`${t`New position`}: ${pos.latitude}, ${pos.longitude}`);
          }

          return (
            <Stack gap={0}>
              {record.note && <Text size='sm'>{record.note}</Text>}
              {extra.map((line) => (
                <Text size='xs' c='dimmed' key={line}>
                  {line}
                </Text>
              ))}
            </Stack>
          );
        }
      },
      UserColumn({
        accessor: 'created_by_detail',
        title: t`By`,
        sortable: false
      })
    ],
    []
  );

  return (
    <InvenTreeTable
      url={apiUrl(ApiEndpoints.fleet_task_action_list)}
      tableState={table}
      columns={columns}
      props={{
        params: { task: taskId },
        enableSearch: false
      }}
    />
  );
}
