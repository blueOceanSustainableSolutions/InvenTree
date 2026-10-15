import { t } from '@lingui/core/macro';
import { Alert, Group, Stack, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { formatDecimal } from '../../defaults/formatters';
import {
  DescriptionColumn,
  IPNColumn,
  PartColumn
} from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { StockItemTable } from '../../tables/stock/StockItemTable';

/** A quantity with the units of its part */
function QuantityValue({
  value,
  units
}: Readonly<{ value: number; units?: string }>) {
  return (
    <Group gap={4} wrap='nowrap'>
      <Text size='sm'>{formatDecimal(value)}</Text>
      {units && <Text size='xs'>[{units}]</Text>}
    </Group>
  );
}

/** The material a build order requires (read only, no allocation) */
function RequiredMaterialTable({ build }: Readonly<{ build: any }>) {
  const table = useTable('fleet-device-required-material');

  const columns: TableColumn[] = useMemo(
    () => [
      PartColumn({ part: 'part_detail', ordering: 'part' }),
      IPNColumn({}),
      DescriptionColumn({ accessor: 'part_detail.description' }),
      {
        accessor: 'reference',
        title: t`Reference`,
        sortable: true
      },
      {
        accessor: 'unit_quantity',
        title: t`Per Device`,
        sortable: true,
        render: (record: any) => (
          <QuantityValue
            value={
              record.bom_item_detail?.quantity ??
              (build.quantity
                ? record.quantity / build.quantity
                : record.quantity)
            }
            units={record.part_detail?.units}
          />
        )
      },
      {
        accessor: 'quantity',
        title: t`Required`,
        sortable: true,
        render: (record: any) => (
          <QuantityValue
            value={record.quantity}
            units={record.part_detail?.units}
          />
        )
      }
    ],
    [build]
  );

  return (
    <InvenTreeTable
      url={apiUrl(ApiEndpoints.build_line_list)}
      tableState={table}
      columns={columns}
      props={{
        params: {
          build: build.pk,
          part_detail: true,
          bom_item_detail: true
        },
        enableDownload: true,
        modelType: ModelType.part,
        modelField: 'part'
      }}
    />
  );
}

/**
 * What the device is made of, to prepare its deployment (e.g. the boat
 * size): the material its build order requires while it is being built,
 * the stock its build order consumed once it is built. Read only: the fleet
 * does not change build orders.
 */
export function DeviceMaterialsPanel({
  deployment
}: Readonly<{ deployment: any }>) {
  const device = deployment.device_detail;
  const builtBy = device?.build;
  const quickBuiltBy = device?.quick_build;

  if (device) {
    if (quickBuiltBy) {
      return (
        <Stack gap='xs'>
          <Text size='sm' c='dimmed'>
            {t`Stock consumed by the quick build which made this device.`}
          </Text>
          <StockItemTable
            tableName='fleet-device-quick-build-consumed-stock'
            allowAdd={false}
            showLocation={false}
            showPricing={false}
            defaultInStock={null}
            params={{ consumed_by_quick_build: quickBuiltBy }}
          />
        </Stack>
      );
    }

    if (!builtBy) {
      return (
        <Alert color='blue' icon={<IconInfoCircle />}>
          {t`This device has no build order or quick build, so its consumed stock is not known.`}
        </Alert>
      );
    }

    return (
      <Stack gap='xs'>
        <Text size='sm' c='dimmed'>
          {t`Stock consumed by the build order which made this device. If the build order made several devices, this is the stock of all of them.`}
        </Text>
        <StockItemTable
          tableName='fleet-device-consumed-stock'
          allowAdd={false}
          showLocation={false}
          showPricing={false}
          defaultInStock={null}
          params={{ consumed_by: builtBy }}
        />
      </Stack>
    );
  }

  if (deployment.build_detail) {
    return (
      <Stack gap='xs'>
        <Text size='sm' c='dimmed'>
          {t`Material required by the build order. "Required" is for the whole build order; "Per Device" is for one device.`}
        </Text>
        <RequiredMaterialTable build={deployment.build_detail} />
      </Stack>
    );
  }

  return (
    <Alert color='orange' icon={<IconInfoCircle />}>
      {t`The device is not built and has no build order yet.`}
    </Alert>
  );
}
