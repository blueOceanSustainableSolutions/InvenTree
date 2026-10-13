import { t } from '@lingui/core/macro';
import { Text } from '@mantine/core';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { DateColumn, UserColumn } from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { PositionValue } from '../coordinates';

/** A location with its depth, or "Not set" */
function LocationCell({
  latitude,
  longitude,
  depth
}: Readonly<{ latitude: any; longitude: any; depth: any }>) {
  if (latitude == null || longitude == null) {
    return (
      <Text size='sm' c='dimmed'>
        {t`Not set`}
      </Text>
    );
  }

  return (
    <PositionValue
      latitude={latitude}
      longitude={longitude}
      suffix={depth != null ? ` (${depth} m)` : ''}
      compact
    />
  );
}

/**
 * The location history of a site: every move, when it happened, and why.
 * The newest move is the current location of the site.
 */
export function SiteLocationTable({
  siteId,
  tableName
}: Readonly<{ siteId: number; tableName?: string }>) {
  const table = useTable(tableName ?? 'fleet-site-locations');

  const columns: TableColumn[] = useMemo(
    () => [
      DateColumn({
        accessor: 'effective_at',
        title: t`Moved At`,
        extra: { showTime: true }
      }),
      {
        accessor: 'old_latitude',
        title: t`From`,
        sortable: false,
        render: (record: any) => (
          <LocationCell
            latitude={record.old_latitude}
            longitude={record.old_longitude}
            depth={record.old_depth_m}
          />
        )
      },
      {
        accessor: 'latitude',
        title: t`To`,
        sortable: false,
        switchable: false,
        render: (record: any) => (
          <LocationCell
            latitude={record.latitude}
            longitude={record.longitude}
            depth={record.depth_m}
          />
        )
      },
      {
        accessor: 'distance_m',
        title: t`Distance`,
        sortable: false,
        render: (record: any) =>
          record.distance_m != null ? `${Math.round(record.distance_m)} m` : '-'
      },
      {
        accessor: 'comment',
        title: t`Reason`,
        sortable: false,
        switchable: false
      },
      UserColumn({
        accessor: 'created_by_detail',
        title: t`By`,
        sortable: false
      }),
      DateColumn({
        accessor: 'created_at',
        title: t`Recorded At`,
        defaultVisible: false,
        extra: { showTime: true }
      })
    ],
    []
  );

  return (
    <InvenTreeTable
      url={apiUrl(ApiEndpoints.fleet_site_location_list)}
      tableState={table}
      columns={columns}
      props={{
        params: { site: siteId },
        enableSearch: false
      }}
    />
  );
}
