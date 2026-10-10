import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { useMemo } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { useSiteFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import {
  BooleanColumn,
  CompanyColumn,
  ReferenceColumn
} from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { CoverageBadge } from '../components/FleetBadges';

/**
 * Table of fleet sites (the places where devices are deployed)
 */
export function SiteTable() {
  const table = useTable('fleet-sites');
  const user = useUserState();

  const siteFields = useSiteFields();

  const newSite = useCreateApiFormModal({
    url: ApiEndpoints.fleet_site_list,
    title: t`Add Site`,
    fields: siteFields,
    follow: true,
    modelType: ModelType.site,
    table: table
  });

  const columns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({}),
      {
        accessor: 'name',
        title: t`Name`,
        sortable: true,
        switchable: false
      },
      {
        accessor: 'coverage',
        title: t`Coverage`,
        sortable: true,
        render: (record: any) => <CoverageBadge coverage={record.coverage} />
      },
      {
        accessor: 'client',
        title: t`Client`,
        sortable: true,
        render: (record: any) =>
          record.client_detail ? (
            <CompanyColumn company={record.client_detail} />
          ) : (
            '-'
          )
      },
      {
        accessor: 'project',
        title: t`Project`,
        sortable: true
      },
      {
        accessor: 'active_deployment_reference',
        title: t`Active Deployment`,
        sortable: false,
        render: (record: any) =>
          record.active_deployment_reference ?? (
            <Text size='sm' c='dimmed'>
              -
            </Text>
          )
      },
      {
        accessor: 'deployment_count',
        title: t`Deployments`,
        sortable: true
      },
      {
        accessor: 'latitude',
        title: t`Latitude`,
        sortable: false,
        defaultVisible: false
      },
      {
        accessor: 'longitude',
        title: t`Longitude`,
        sortable: false,
        defaultVisible: false
      },
      BooleanColumn({
        accessor: 'active',
        title: t`Active`
      })
    ],
    []
  );

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'active',
        label: t`Active`,
        description: t`Show active sites`
      },
      {
        name: 'has_active_deployment',
        label: t`Has Active Deployment`,
        description: t`Show sites with a device deployed now`
      },
      {
        name: 'coverage',
        label: t`Coverage`,
        description: t`Filter by service coverage`,
        choices: [
          { value: 'FULL', label: t`Full service` },
          { value: 'NO_SERVICE', label: t`Monitored only` },
          { value: 'THIRD_PARTY', label: t`Third party` }
        ]
      }
    ],
    []
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-site'
        hidden={!user.hasAddRole(UserRoles.fleet)}
        tooltip={t`Add site`}
        onClick={() => newSite.open()}
      />
    ],
    [user, newSite]
  );

  return (
    <>
      {newSite.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_site_list)}
        tableState={table}
        columns={columns}
        props={{
          tableFilters: filters,
          tableActions: tableActions,
          enableDownload: true,
          modelType: ModelType.site
        }}
      />
    </>
  );
}
