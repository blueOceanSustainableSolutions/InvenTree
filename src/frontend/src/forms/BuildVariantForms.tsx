import { t } from '@lingui/core/macro';
import { Alert, Stack, Table, Text } from '@mantine/core';
import {
  IconCalendar,
  IconInfoCircle,
  IconList,
  IconSitemap,
  IconTruckDelivery,
  IconUsersGroup
} from '@tabler/icons-react';
import { useMemo, useRef, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { formatDecimal } from '@lib/functions/Formatting';
import type { ApiFormFieldSet, ApiFormFieldType } from '@lib/types/Forms';
import { api } from '../App';
import type { TableFieldRowProps } from '../components/forms/fields/TableField';
import { StandaloneField } from '../components/forms/StandaloneField';
import { RenderPart } from '../components/render/Part';
import { useCreateApiFormModal } from '../hooks/UseForm';
import { useGlobalSettingsState } from '../states/SettingsStates';
import { RenderPartColumn } from '../tables/ColumnRenderers';
import { useBuildOrderFields } from './BuildForms';

function BuildVariantRow({ row }: Readonly<{ row: TableFieldRowProps }>) {
  const item = row.item;

  const quantityField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'number',
      value: item.quantity,
      onValueChange: (value: any) => row.changeFn(row.idx, 'quantity', value)
    }),
    [item.quantity, row.idx]
  );

  return (
    <Table.Tr>
      <Table.Td>
        <Stack gap={0}>
          <RenderPartColumn part={item.part_detail} />
          {item.part_detail?.trackable && (
            <Text size='xs' c='dimmed'>
              {t`Trackable: one build order per unit`}
            </Text>
          )}
          {row.rowErrors?.part?.message && (
            <Text size='xs' c='red'>
              {row.rowErrors.part.message}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>{formatDecimal(item.part_detail?.in_stock ?? 0)}</Table.Td>
      <Table.Td>
        <StandaloneField
          fieldName='quantity'
          hideLabels
          fieldDefinition={quantityField}
          error={row.rowErrors?.quantity?.message}
        />
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * Form to build variants of a template part. A template is never built itself:
 * one build order is created per variant (one per unit for trackable variants).
 */
export function useBuildVariantsForm({
  onFormSuccess
}: {
  onFormSuccess?: (data: any) => void;
}) {
  const globalSettings = useGlobalSettingsState();

  const [template, setTemplate] = useState<any>(null);
  const [initialData, setInitialData] = useState<any>({});

  const fields: ApiFormFieldSet = useMemo(
    () => ({
      part: { hidden: true },
      build_line: { hidden: true },
      items: {
        field_type: 'table',
        headers: [
          { title: t`Variant`, style: { minWidth: '220px' } },
          { title: t`In Stock`, style: { minWidth: '90px' } },
          { title: t`Build Quantity`, style: { width: '100%' } }
        ],
        modelRenderer: (row: TableFieldRowProps) => (
          <BuildVariantRow key={row.idx} row={row} />
        )
      },
      title: {},
      project_code: {
        icon: <IconList />,
        hidden: !globalSettings.isSet('PROJECT_CODES_ENABLED', true)
      },
      priority: {},
      parent: {
        icon: <IconSitemap />,
        filters: { part_detail: true }
      },
      sales_order: { icon: <IconTruckDelivery /> },
      start_date: { icon: <IconCalendar /> },
      target_date: { icon: <IconCalendar /> },
      take_from: {},
      destination: { filters: { structural: false } },
      responsible: {
        icon: <IconUsersGroup />,
        filters: { is_active: true }
      }
    }),
    [globalSettings]
  );

  const preFormContent = useMemo(
    () =>
      template && (
        <Alert
          color='blue'
          icon={<IconInfoCircle />}
          title={t`Build variants of a template`}
        >
          <Stack gap='xs'>
            <RenderPart instance={template} />
            <Text size='sm'>
              {t`Template parts are not built. Enter how many of each variant to build: one build order is created per variant.`}
            </Text>
            {initialData.quantity > 0 && (
              <Text size='sm'>
                {t`Required`}: {formatDecimal(initialData.quantity)}
              </Text>
            )}
            {(initialData.items ?? []).length == 0 && (
              <Text size='sm' c='red'>
                {t`This template has no buildable variants.`}
              </Text>
            )}
          </Stack>
        </Alert>
      ),
    [template, initialData]
  );

  const form = useCreateApiFormModal({
    url: ApiEndpoints.build_order_variants,
    title: t`Build Variants`,
    modalId: 'build-template-variants',
    fields: fields,
    initialData: initialData,
    preFormContent: preFormContent,
    size: '80%',
    submitText: t`Create Build Orders`,
    successMessage: t`Build orders created`,
    onFormSuccess: onFormSuccess
  });

  return {
    ...form,
    /**
     * Open the form for a template part.
     * Other build order fields in `data` (e.g. parent, sales_order, quantity) are kept.
     */
    open: (part: any, data: Record<string, any> = {}) => {
      api
        .get(apiUrl(ApiEndpoints.part_list), {
          params: {
            ancestor: part.pk,
            assembly: true,
            is_template: false,
            virtual: false,
            active: globalSettings.isSet('BUILDORDER_REQUIRE_ACTIVE_PART')
              ? true
              : undefined
          }
        })
        .then((response) => {
          const variants: any[] = response.data ?? [];

          // With a single variant, it simply builds the required quantity
          const quantity =
            variants.length == 1 && data.quantity > 0 ? data.quantity : null;

          setTemplate(part);
          setInitialData({
            ...data,
            part: part.pk,
            items: variants.map((variant: any) => ({
              part: variant.pk,
              part_detail: variant,
              quantity: quantity
            }))
          });
          form.open();
        });
    }
  };
}

/**
 * Create a build order. A template part is never built: selecting one (or opening
 * the form for one) switches to building its variants.
 */
export function useCreateBuildOrderForm({
  modalId,
  title,
  keepOpenOption,
  onFormSuccess
}: {
  modalId: string;
  title: string;
  keepOpenOption?: boolean;
  onFormSuccess?: (data: any) => void;
}) {
  const variants = useBuildVariantsForm({ onFormSuccess });

  const [initialData, setInitialData] = useState<Record<string, any>>({});
  const [variantData, setVariantData] = useState<Record<string, any>>({});

  // Always reads the latest data, as the field callbacks are memoized
  const switchToVariants = useRef<(part: any) => void>(() => {});

  const fields = useBuildOrderFields({
    create: true,
    modalId: modalId,
    onTemplatePart: (part: any) => switchToVariants.current(part)
  });

  const form = useCreateApiFormModal({
    url: ApiEndpoints.build_order_list,
    title: title,
    modalId: modalId,
    fields: fields,
    initialData: initialData,
    follow: true,
    modelType: ModelType.build,
    keepOpenOption: keepOpenOption,
    onFormSuccess: onFormSuccess
  });

  switchToVariants.current = (part: any) => {
    form.close();
    variants.open(part, { ...initialData, ...variantData });
  };

  return {
    modal: (
      <>
        {form.modal}
        {variants.modal}
      </>
    ),
    /**
     * Open the form.
     * @param data - initial build order data
     * @param part - the part detail, when known (a template opens its variants directly)
     * @param variantData - data which only applies when building variants (e.g. build_line)
     */
    open: ({
      data = {},
      part,
      variantData = {}
    }: {
      data?: Record<string, any>;
      part?: any;
      variantData?: Record<string, any>;
    } = {}) => {
      const openFor = (instance?: any) => {
        if (instance?.is_template) {
          variants.open(instance, { ...data, ...variantData });
          return;
        }

        setInitialData(data);
        setVariantData(variantData);
        form.open();
      };

      // Only the part ID is known: look it up to see if it is a template
      if (!part && data.part) {
        api
          .get(apiUrl(ApiEndpoints.part_list, data.part))
          .then((response) => openFor(response.data))
          .catch(() => openFor());
        return;
      }

      openFor(part);
    }
  };
}
