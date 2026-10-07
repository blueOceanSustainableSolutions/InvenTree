import { t } from '@lingui/core/macro';
import { Badge, Group, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { formatDecimal } from '@lib/functions/Formatting';
import { getDetailUrl } from '@lib/functions/Navigation';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { useQuickBuildForm } from '../../forms/QuickBuildForms';
import { useUserState } from '../../states/UserState';
import {
  CreationDateColumn,
  DateColumn,
  IPNColumn,
  LocationColumn,
  PartColumn,
  ReferenceColumn,
  UserColumn
} from '../ColumnRenderers';
import { InvenTreeTable } from '../InvenTreeTable';

/**
 * Table of quick builds (fractional assemblies made without a build order)
 */
export function QuickBuildTable({ part }: Readonly<{ part?: any }>) {
  const partId = part?.pk;
  const table = useTable(partId ? 'quick-build-part' : 'quick-build-index');
  const user = useUserState();
  const navigate = useNavigate();

  const quickBuild = useQuickBuildForm({ part, table });

  const tableColumns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({}),
      PartColumn({ switchable: false }),
      IPNColumn({}),
      {
        accessor: 'quantity',
        title: t`Quantity`,
        sortable: true,
        switchable: false,
        render: (record: any) => (
          <Text size='sm'>
            {formatDecimal(record.quantity)} {record.part_detail?.units}
          </Text>
        )
      },
      {
        accessor: 'lines',
        title: t`Consumed`,
        sortable: false,
        render: (record: any) => (
          <Stack gap={0}>
            {(record.lines ?? []).map((line: any) => (
              <Text size='xs' key={line.pk}>
                {formatDecimal(line.quantity)} {line.part_detail?.units}{' '}
                {line.part_detail?.full_name ?? line.part_detail?.name}
                {line.loss > 0 &&
                  ` (${t`loss`} ${formatDecimal(line.loss)} ${line.part_detail?.units ?? ''})`}
              </Text>
            ))}
          </Stack>
        )
      },
      LocationColumn({ accessor: 'location_detail' }),
      {
        accessor: 'disassembled',
        title: t`Status`,
        sortable: true,
        render: (record: any) =>
          record.disassembled ? (
            <Group gap='xs'>
              <Badge color='gray'>{t`Disassembled`}</Badge>
            </Group>
          ) : (
            <Badge color='green'>{t`Built`}</Badge>
          )
      },
      CreationDateColumn({ extra: { showTime: true } }),
      DateColumn({
        accessor: 'disassembled_date',
        title: t`Disassembled Date`,
        defaultVisible: false,
        extra: { showTime: true }
      }),
      UserColumn({
        accessor: 'created_by_detail',
        ordering: 'created_by',
        title: t`Created By`
      }),
      {
        accessor: 'notes',
        title: t`Notes`,
        sortable: false
      }
    ],
    []
  );

  const tableFilters: TableFilter[] = useMemo(
    () => [
      {
        name: 'disassembled',
        label: t`Disassembled`,
        description: t`Show quick builds which have been disassembled`
      }
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='quick-build'
        hidden={!user.hasAddRole(UserRoles.build)}
        tooltip={t`New Quick Build`}
        text={t`Quick Build`}
        onClick={() => quickBuild.open()}
      />
    ],
    [user, quickBuild]
  );

  return (
    <>
      {quickBuild.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.quick_build_list)}
        tableState={table}
        columns={tableColumns}
        props={{
          params: { part: partId },
          tableActions: tableActions,
          tableFilters: tableFilters,
          enableDownload: true,
          onRowClick: (record: any) => {
            if (record.output) {
              navigate(getDetailUrl(ModelType.stockitem, record.output));
            }
          }
        }}
      />
    </>
  );
}
