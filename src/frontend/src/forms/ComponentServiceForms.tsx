import { t } from '@lingui/core/macro';
import { Alert } from '@mantine/core';
import { type ReactNode, useCallback, useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import type { ApiFormFieldSet, ApiFormFieldType } from '@lib/types/Forms';
import type { TableState } from '@lib/types/Tables';
import { useCreateApiFormModal } from '../hooks/UseForm';

export type ComponentServiceForms = {
  openInstall: () => void;
  openRemove: (component: any) => void;
  openDestroy: (component: any) => void;
  openReplace: (component: any) => void;
  modals: ReactNode;
};

/**
 * The component service forms of a finished unit: add, remove, destroy and
 * replace a component (see stock/components.py).
 *
 * Used by the installed items table of a stock item (stock/<pk>/components/)
 * and by fleet maintenance tasks (fleet/task/<pk>/component-action/), which
 * accept the same input plus a few extra fields.
 *
 * @param unit - The finished unit (stock item) being serviced
 * @param url - Action endpoint (default: stock/:id/components/)
 * @param pk - Primary key for the endpoint (default: the unit)
 * @param extraFields - Extra fields for every form (e.g. a fault code)
 * @param removalFields - Extra fields for the remove and destroy forms only
 * @param defaultLocation - Default location for a removal (default: the unit location, else the part default location; null leaves it blank)
 * @param location - Overrides for the location field (e.g. a description)
 * @param table - Table to refresh after a change
 * @param onSuccess - Called after a change
 */
export function useComponentServiceForms({
  unit,
  url = ApiEndpoints.stock_components,
  pk,
  extraFields = {},
  removalFields = {},
  defaultLocation,
  location = {},
  table,
  onSuccess
}: {
  unit: any;
  url?: ApiEndpoints | string;
  pk?: number | string;
  extraFields?: ApiFormFieldSet;
  removalFields?: ApiFormFieldSet;
  defaultLocation?: number | null;
  location?: Partial<ApiFormFieldType>;
  table?: TableState;
  onSuccess?: () => void;
}): ComponentServiceForms {
  const [selectedRecord, setSelectedRecord] = useState<any>({});

  const formUrl = apiUrl(url);
  const formPk = pk ?? unit.pk;

  const removalLocation =
    defaultLocation === undefined
      ? (unit.location ?? unit.part_detail?.default_location)
      : defaultLocation;

  const stockItemField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'related field',
      model: ModelType.stockitem,
      api_url: apiUrl(ApiEndpoints.stock_item_list),
      filters: {
        part_detail: true,
        location_detail: true,
        in_stock: true,
        available: true
      }
    }),
    []
  );

  const locationField: ApiFormFieldType = useMemo(
    () => ({
      filters: { structural: false },
      ...location
    }),
    [location]
  );

  const installItem = useCreateApiFormModal({
    url: formUrl,
    pk: formPk,
    title: t`Add Component`,
    table: table,
    successMessage: t`Component installed`,
    onFormSuccess: onSuccess,
    fields: {
      action: {
        hidden: true
      },
      stock_item: stockItemField,
      quantity: {},
      notes: {},
      ...extraFields
    },
    initialData: {
      action: 'add',
      quantity: 1
    }
  });

  const uninstallItem = useCreateApiFormModal({
    url: formUrl,
    pk: formPk,
    title: t`Remove Component`,
    table: table,
    successMessage: t`Component removed`,
    onFormSuccess: onSuccess,
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      location: locationField,
      disposition: {},
      notes: {},
      ...extraFields,
      ...removalFields
    },
    initialData: {
      action: 'remove',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1,
      location: removalLocation,
      disposition: 'keep'
    }
  });

  const destroyItem = useCreateApiFormModal({
    url: formUrl,
    pk: formPk,
    title: t`Mark Component Destroyed`,
    table: table,
    successMessage: t`Component marked as destroyed`,
    onFormSuccess: onSuccess,
    preFormContent: (
      <Alert color='red'>
        {t`The component remains installed and is marked as destroyed. You can remove or replace it separately.`}
      </Alert>
    ),
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      notes: {},
      ...extraFields,
      ...removalFields
    },
    initialData: {
      action: 'destroy',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1
    }
  });

  const replaceItem = useCreateApiFormModal({
    url: formUrl,
    pk: formPk,
    title: t`Replace Component`,
    table: table,
    successMessage: t`Component replaced`,
    onFormSuccess: onSuccess,
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      stock_item: stockItemField,
      replacement_quantity: {},
      location: locationField,
      disposition: {},
      notes: {},
      ...extraFields
    },
    initialData: {
      action: 'replace',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1,
      replacement_quantity: selectedRecord.quantity ?? 1,
      location: removalLocation,
      disposition: 'keep'
    }
  });

  const openInstall = useCallback(() => installItem.open(), [installItem]);

  const openRemove = useCallback(
    (component: any) => {
      setSelectedRecord(component);
      uninstallItem.open();
    },
    [uninstallItem]
  );

  const openDestroy = useCallback(
    (component: any) => {
      setSelectedRecord(component);
      destroyItem.open();
    },
    [destroyItem]
  );

  const openReplace = useCallback(
    (component: any) => {
      setSelectedRecord(component);
      replaceItem.open();
    },
    [replaceItem]
  );

  return {
    openInstall,
    openRemove,
    openDestroy,
    openReplace,
    modals: (
      <>
        {installItem.modal}
        {uninstallItem.modal}
        {destroyItem.modal}
        {replaceItem.modal}
      </>
    )
  };
}
