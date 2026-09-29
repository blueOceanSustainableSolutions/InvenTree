import { t } from '@lingui/core/macro';
import { Alert, Skeleton } from '@mantine/core';
import {
  IconFlame,
  IconReplace,
  IconUnlink
} from '@tabler/icons-react';
import { useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { PartColumn, StatusColumn, StockColumn } from '../ColumnRenderers';
import { InvenTreeTable } from '../InvenTreeTable';

export default function InstalledItemsTable({
  stockItem
}: Readonly<{
  stockItem: any;
}>) {
  const table = useTable('stock_item_install');
  const user = useUserState();

  const installItem = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.stock_components),
    pk: stockItem.pk,
    title: t`Add Component`,
    table: table,
    successMessage: t`Component installed`,
    fields: {
      action: {
        hidden: true
      },
      stock_item: {
        field_type: 'related field',
        model: ModelType.stockitem,
        api_url: apiUrl(ApiEndpoints.stock_item_list),
        filters: {
          part_detail: true,
          location_detail: true,
          in_stock: true,
          available: true
        }
      },
      quantity: {},
      notes: {}
    },
    initialData: {
      action: 'add',
      quantity: 1
    }
  });

  const [selectedRecord, setSelectedRecord] = useState<any>({});

  const uninstallItem = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.stock_components),
    pk: stockItem.pk,
    title: t`Remove Component`,
    table: table,
    successMessage: t`Component removed`,
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      location: {
        filters: { structural: false }
      },
      disposition: {},
      notes: {}
    },
    initialData: {
      action: 'remove',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1,
      location: stockItem.location ?? stockItem.part_detail?.default_location,
      disposition: 'keep'
    }
  });

  const destroyItem = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.stock_components),
    pk: stockItem.pk,
    title: t`Mark Component Destroyed`,
    table: table,
    successMessage: t`Component marked as destroyed`,
    preFormContent: (
      <Alert color='red'>
        {t`The component remains installed and is marked as destroyed. You can remove or replace it separately.`}
      </Alert>
    ),
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      notes: {}
    },
    initialData: {
      action: 'destroy',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1
    }
  });

  const replaceItem = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.stock_components),
    pk: stockItem.pk,
    title: t`Replace Component`,
    table: table,
    successMessage: t`Component replaced`,
    fields: {
      action: { hidden: true },
      component: { hidden: true },
      quantity: {},
      stock_item: {
        field_type: 'related field',
        model: ModelType.stockitem,
        api_url: apiUrl(ApiEndpoints.stock_item_list),
        filters: {
          part_detail: true,
          location_detail: true,
          in_stock: true,
          available: true
        }
      },
      replacement_quantity: {},
      location: {
        filters: { structural: false }
      },
      disposition: {},
      notes: {}
    },
    initialData: {
      action: 'replace',
      component: selectedRecord.pk,
      quantity: selectedRecord.quantity ?? 1,
      replacement_quantity: selectedRecord.quantity ?? 1,
      location: stockItem.location ?? stockItem.part_detail?.default_location,
      disposition: 'keep'
    }
  });

  const tableColumns: TableColumn[] = useMemo(() => {
    return [
      PartColumn({
        part: 'part_detail'
      }),
      StockColumn({
        accessor: '',
        title: t`Stock Item`,
        sortable: false
      }),
      {
        accessor: 'batch',
        switchable: false
      },
      StatusColumn({ model: ModelType.stockitem })
    ];
  }, []);

  const tableActions = useMemo(() => {
    return [
      <AddItemButton
        key='install'
        tooltip={t`Add Component`}
        onClick={() => {
          installItem.open();
        }}
        hidden={
          !user.hasChangeRole(UserRoles.stock) ||
          stockItem.is_building ||
          Number(stockItem.quantity) !== 1
        }
      />
    ];
  }, [stockItem, user]);

  const rowActions = useCallback(
    (record: any) => {
      return [
        {
          title: t`Uninstall`,
          tooltip: t`Uninstall stock item`,
          onClick: () => {
            setSelectedRecord(record);
            uninstallItem.open();
          },
          icon: <IconUnlink />,
          hidden: !user.hasChangeRole(UserRoles.stock)
        },
        {
          title: t`Mark Destroyed`,
          tooltip: t`Keep the component installed and mark it as destroyed`,
          onClick: () => {
            setSelectedRecord(record);
            destroyItem.open();
          },
          icon: <IconFlame />,
          color: 'red',
          hidden:
            !user.hasChangeRole(UserRoles.stock) || record.status == 60
        },
        {
          title: t`Replace Component`,
          tooltip: t`Remove this component and install another stock item`,
          onClick: () => {
            setSelectedRecord(record);
            replaceItem.open();
          },
          icon: <IconReplace />,
          hidden: !user.hasChangeRole(UserRoles.stock)
        }
      ];
    },
    [user]
  );

  return (
    <>
      {installItem.modal}
      {uninstallItem.modal}
      {destroyItem.modal}
      {replaceItem.modal}
      {stockItem.pk ? (
        <InvenTreeTable
          url={apiUrl(ApiEndpoints.stock_item_list)}
          tableState={table}
          columns={tableColumns}
          props={{
            tableActions: tableActions,
            enableSelection: true,
            enableLabels: true,
            enableReports: true,
            rowActions: rowActions,
            modelType: ModelType.stockitem,
            params: {
              belongs_to: stockItem.pk,
              part_detail: true
            }
          }}
        />
      ) : (
        <Skeleton />
      )}
    </>
  );
}
