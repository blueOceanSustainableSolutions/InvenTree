import { t } from '@lingui/core/macro';
import { Alert, Stack, Table, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { formatDecimal } from '@lib/functions/Formatting';
import type { ApiFormFieldSet, ApiFormFieldType } from '@lib/types/Forms';
import type { TableFieldRowProps } from '../components/forms/fields/TableField';
import { StandaloneField } from '../components/forms/StandaloneField';
import { RenderPart } from '../components/render/Part';
import { useCreateApiFormModal } from '../hooks/UseForm';

/** Format a quantity with the units of the part. */
function quantityWithUnits(quantity: number, units?: string): string {
  return units
    ? `${formatDecimal(quantity)} ${units}`
    : `${formatDecimal(quantity)}`;
}

function QuickBuildItemRow({ row }: Readonly<{ row: TableFieldRowProps }>) {
  const item = row.item;

  const stockField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'related field',
      api_url: apiUrl(ApiEndpoints.stock_item_list),
      model: ModelType.stockitem,
      required: !item.optional,
      filters: {
        bom_item: item.bom_item,
        available: true,
        part_detail: true,
        location_detail: true
      },
      value: item.stock_item,
      onValueChange: (value: any) => row.changeFn(row.idx, 'stock_item', value)
    }),
    [item.bom_item, item.optional, item.stock_item, row.idx]
  );

  return (
    <Table.Tr>
      <Table.Td>
        <Stack gap={0}>
          <Text size='sm'>{item.part_name}</Text>
          {item.part_ipn && (
            <Text size='xs' c='dimmed'>
              {item.part_ipn}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Stack gap={0}>
          <Text size='sm'>{quantityWithUnits(item.required, item.units)}</Text>
          {item.setup_quantity > 0 && (
            <Text size='xs' c='dimmed'>
              {t`Includes loss`}:{' '}
              {quantityWithUnits(item.setup_quantity, item.units)}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        <StandaloneField
          fieldName='stock_item'
          hideLabels
          fieldDefinition={stockField}
          error={row.rowErrors?.stock_item?.message}
        />
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * Two-stage form for performing a quick build:
 * 1. Select the assembly and the (fractional) quantity
 * 2. Confirm the source stock for each BOM line, then build
 */
export function useQuickBuildForm({
  part,
  table,
  onFormSuccess
}: {
  part?: any;
  table?: any;
  onFormSuccess?: (data: any) => void;
}) {
  const [requirements, setRequirements] = useState<any>();
  const [selectedPart, setSelectedPart] = useState<any>(part);

  const buildFields: ApiFormFieldSet = useMemo(
    () => ({
      part: { hidden: true, value: requirements?.part },
      quantity: { hidden: true, value: requirements?.quantity },
      location: {
        filters: { structural: false },
        value: selectedPart?.default_location
      },
      batch: {},
      notes: {},
      items: {
        field_type: 'table',
        value: requirements?.items ?? [],
        headers: [
          { title: t`Component`, style: { minWidth: '180px' } },
          { title: t`Required`, style: { minWidth: '130px' } },
          { title: t`Source Stock`, style: { width: '100%' } }
        ],
        modelRenderer: (row: TableFieldRowProps) => (
          <QuickBuildItemRow key={row.idx} row={row} />
        )
      }
    }),
    [requirements, selectedPart]
  );

  const preFormContent = useMemo(
    () =>
      requirements && (
        <Alert color='blue' icon={<IconInfoCircle />} title={t`Quick Build`}>
          <Stack gap='xs'>
            {selectedPart && <RenderPart instance={selectedPart} />}
            <Text size='sm'>
              {t`Quantity`}:{' '}
              {quantityWithUnits(requirements.quantity, selectedPart?.units)}
            </Text>
          </Stack>
        </Alert>
      ),
    [requirements, selectedPart]
  );

  const build = useCreateApiFormModal({
    url: ApiEndpoints.quick_build_create,
    title: t`Quick Build`,
    modalId: 'perform-quick-build',
    fields: buildFields,
    preFormContent: preFormContent,
    initialData: {
      part: requirements?.part,
      quantity: requirements?.quantity,
      location: selectedPart?.default_location,
      items: requirements?.items ?? []
    },
    size: '80%',
    submitText: t`Build`,
    table,
    successMessage: t`Quick build completed`,
    onFormSuccess
  });

  const select = useCreateApiFormModal({
    url: ApiEndpoints.quick_build_requirements,
    title: t`Quick Build`,
    modalId: 'select-quick-build-part',
    fields: {
      part: {
        value: part?.pk,
        hidden: !!part?.pk,
        filters: { quick_build: true, active: true },
        onValueChange: (_value: any, instance: any) => setSelectedPart(instance)
      },
      quantity: {}
    },
    initialData: { part: part?.pk },
    submitText: t`Continue`,
    successMessage: null,
    onFormSuccess: (data: any) => {
      setRequirements(data);
      setTimeout(() => build.open(), 0);
    }
  });

  return {
    open: () => {
      setSelectedPart(part);
      select.open();
    },
    close: select.close,
    toggle: select.toggle,
    modal: (
      <>
        {select.modal}
        {build.modal}
      </>
    )
  };
}
