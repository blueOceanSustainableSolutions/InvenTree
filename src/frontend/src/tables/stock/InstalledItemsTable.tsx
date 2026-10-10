import { t } from '@lingui/core/macro';
import { Skeleton } from '@mantine/core';
import { IconFlame, IconReplace, IconUnlink } from '@tabler/icons-react';
import { useCallback, useMemo } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { useComponentServiceForms } from '../../forms/ComponentServiceForms';
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

  const serviceForms = useComponentServiceForms({
    unit: stockItem,
    table: table
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
          serviceForms.openInstall();
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
            serviceForms.openRemove(record);
          },
          icon: <IconUnlink />,
          hidden: !user.hasChangeRole(UserRoles.stock)
        },
        {
          title: t`Mark Destroyed`,
          tooltip: t`Keep the component installed and mark it as destroyed`,
          onClick: () => {
            serviceForms.openDestroy(record);
          },
          icon: <IconFlame />,
          color: 'red',
          hidden: !user.hasChangeRole(UserRoles.stock) || record.status == 60
        },
        {
          title: t`Replace Component`,
          tooltip: t`Remove this component and install another stock item`,
          onClick: () => {
            serviceForms.openReplace(record);
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
      {serviceForms.modals}
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
