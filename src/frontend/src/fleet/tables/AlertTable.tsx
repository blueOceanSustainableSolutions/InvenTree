import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';
import { showNotification } from '@mantine/notifications';
import { IconCircleCheck, IconEye, IconTool } from '@tabler/icons-react';
import { useCallback, useMemo, useState } from 'react';

import type { RowAction } from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { useApi } from '../../contexts/ApiContext';
import {
  alertCreateTaskFields,
  resolveAlertFields
} from '../../forms/FleetForms';
import { showApiErrorMessage } from '../../functions/notifications';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import {
  DateColumn,
  ReferenceColumn,
  StatusColumn,
  UserColumn
} from '../../tables/ColumnRenderers';
import { StatusFilterOptions } from '../../tables/Filter';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import {
  FleetStatus,
  FleetStatusBadge,
  alertTypeChoices,
  alertTypeLabel,
  fleetStatusChoices
} from '../components/FleetBadges';

/**
 * Table of fleet alerts.
 *
 * Alerts are raised by the system (monitoring, planning). Users acknowledge
 * them (someone is on it) or resolve them by hand.
 */
export function AlertTable({
  params = {},
  openOnly = false,
  tableName
}: Readonly<{
  params?: Record<string, any>;
  openOnly?: boolean;
  tableName?: string;
}>) {
  const table = useTable(tableName ?? 'fleet-alerts');
  const user = useUserState();
  const api = useApi();

  const [selected, setSelected] = useState<number | undefined>(undefined);

  const canChange = user.hasChangeRole(UserRoles.fleet);

  const resolveAlert = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_alert_resolve, selected),
    title: t`Resolve Alert`,
    fields: resolveAlertFields(),
    preFormWarning: t`If the condition is still active, the monitor opens a new alert on its next check.`,
    successMessage: t`Alert resolved`,
    table: table
  });

  const createTask = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_alert_create_task, selected),
    title: t`Create Maintenance Task`,
    fields: alertCreateTaskFields(),
    preFormContent: (
      <Text size='sm'>
        {t`Creates a corrective task for the device, linked to this alert. The alert is acknowledged, and resolved when the task is completed.`}
      </Text>
    ),
    successMessage: t`Task created`,
    follow: true,
    modelType: ModelType.maintenancetask,
    table: table
  });

  const acknowledge = useCallback(
    (pk: number) => {
      api
        .post(apiUrl(ApiEndpoints.fleet_alert_acknowledge, pk), {})
        .then(() => {
          table.refreshTable();
          showNotification({
            title: t`Alert acknowledged`,
            message: t`The alert stays open until its condition clears`,
            color: 'green'
          });
        })
        .catch((error) => {
          showApiErrorMessage({
            error: error,
            title: t`Error`,
            message: t`Failed to acknowledge the alert`
          });
        });
    },
    [api, table]
  );

  const columns: TableColumn[] = useMemo(
    () => [
      ReferenceColumn({ switchable: false }),
      {
        accessor: 'severity',
        title: t`Severity`,
        sortable: true,
        render: (record: any) => (
          <FleetStatusBadge
            type={FleetStatus.severity}
            status={record.severity}
          />
        )
      },
      StatusColumn({ model: ModelType.alert }),
      {
        accessor: 'alert_type',
        title: t`Type`,
        sortable: true,
        render: (record: any) => alertTypeLabel(record.alert_type)
      },
      {
        accessor: 'message',
        title: t`Message`,
        sortable: false
      },
      {
        accessor: 'site',
        title: t`Site`,
        sortable: true,
        hidden: !!params.site,
        render: (record: any) => record.site_detail?.name ?? '-'
      },
      {
        accessor: 'deployment',
        title: t`Deployment`,
        sortable: true,
        hidden: !!params.deployment,
        render: (record: any) =>
          record.deployment_detail ? (
            <Stack gap={0}>
              <Text size='sm'>{record.deployment_detail.reference}</Text>
              {record.device_serial && (
                <Text size='xs' c='dimmed'>
                  #{record.device_serial}
                </Text>
              )}
            </Stack>
          ) : (
            '-'
          )
      },
      DateColumn({
        accessor: 'opened_at',
        title: t`Opened`,
        extra: { showTime: true }
      }),
      DateColumn({
        accessor: 'acknowledged_at',
        title: t`Acknowledged`,
        defaultVisible: false,
        extra: { showTime: true }
      }),
      UserColumn({
        accessor: 'acknowledged_by_detail',
        title: t`Acknowledged By`,
        sortable: false,
        defaultVisible: false
      }),
      DateColumn({
        accessor: 'resolved_at',
        title: t`Resolved`,
        hidden: openOnly,
        extra: { showTime: true }
      }),
      UserColumn({
        accessor: 'resolved_by_detail',
        title: t`Resolved By`,
        sortable: false,
        hidden: openOnly,
        defaultVisible: false
      }),
      {
        accessor: 'resolution',
        title: t`Resolution`,
        sortable: false,
        defaultVisible: false
      }
    ],
    [params, openOnly]
  );

  const filters: TableFilter[] = useMemo(() => {
    const result: TableFilter[] = [
      {
        name: 'severity',
        label: t`Severity`,
        description: t`Filter by alert severity`,
        choiceFunction: fleetStatusChoices(FleetStatus.severity)
      },
      {
        name: 'alert_type',
        label: t`Type`,
        description: t`Filter by alert type`,
        choices: alertTypeChoices()
      }
    ];

    if (!openOnly) {
      result.push(
        {
          name: 'open',
          label: t`Open`,
          description: t`Show alerts which are not resolved`
        },
        {
          name: 'status',
          label: t`Status`,
          description: t`Filter by alert status`,
          choiceFunction: StatusFilterOptions(ModelType.alert)
        }
      );
    }

    return result;
  }, [openOnly]);

  const rowActions = useCallback(
    (record: any): RowAction[] => {
      const resolved = record.resolved_at != null;

      return [
        {
          title: t`Acknowledge`,
          icon: <IconEye />,
          color: 'orange',
          hidden: !canChange || resolved || record.acknowledged_at != null,
          onClick: () => acknowledge(record.pk)
        },
        {
          title: t`Create Task`,
          icon: <IconTool />,
          color: 'blue',
          hidden: !canChange || resolved || !record.deployment,
          onClick: () => {
            setSelected(record.pk);
            createTask.open();
          }
        },
        {
          title: t`Resolve`,
          icon: <IconCircleCheck />,
          color: 'green',
          hidden: !canChange || resolved,
          onClick: () => {
            setSelected(record.pk);
            resolveAlert.open();
          }
        }
      ];
    },
    [canChange, acknowledge, resolveAlert, createTask]
  );

  const queryParams = useMemo(() => {
    const result: Record<string, any> = { ...params };

    if (openOnly) {
      result.open = true;
    }

    return result;
  }, [params, openOnly]);

  return (
    <>
      {resolveAlert.modal}
      {createTask.modal}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.fleet_alert_list)}
        tableState={table}
        columns={columns}
        props={{
          params: queryParams,
          tableFilters: filters,
          rowActions: rowActions,
          enableDownload: true,
          modelType: ModelType.deployment,
          modelField: 'deployment'
        }}
      />
    </>
  );
}
