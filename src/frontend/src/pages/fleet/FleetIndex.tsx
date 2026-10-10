import { t } from '@lingui/core/macro';
import { Paper, Stack, Title } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconAnchor,
  IconCalendar,
  IconCalendarEvent,
  IconDashboard,
  IconMapPin,
  IconRadar,
  IconSailboat,
  IconTable,
  IconTimeline,
  IconTool
} from '@tabler/icons-react';
import { useMemo } from 'react';

import { PluginPanelKey } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import type { PanelType } from '@lib/types/Panel';
import PermissionDenied from '../../components/errors/PermissionDenied';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import SegmentedControlPanel from '../../components/panels/SegmentedControlPanel';
import { FleetCalendar } from '../../fleet/components/FleetCalendar';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { OverviewKpis } from '../../fleet/components/OverviewKpis';
import {
  MaintenanceKpis,
  PipelineKpis
} from '../../fleet/components/PipelineKpis';
import { TaskCalendar } from '../../fleet/components/TaskCalendar';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { DeploymentTable } from '../../fleet/tables/DeploymentTable';
import { DeviceTypeTable } from '../../fleet/tables/DeviceTypeTable';
import { SiteTable } from '../../fleet/tables/SiteTable';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { TripTable } from '../../fleet/tables/TripTable';
import { useUserState } from '../../states/UserState';

/**
 * Fleet index page: overview (map and open alerts), alerts, deployment
 * pipeline, deployed devices, maintenance tasks, field trips, calendar,
 * sites and device types.
 *
 * Plan selection: tick open tasks (Maintenance) or ready deployments
 * (Pipeline) and use "Create Field Trip" / "Add to Trip".
 */
export default function FleetIndex() {
  const user = useUserState();

  const [maintenanceView, setMaintenanceView] = useLocalStorage<string>({
    key: 'fleet-maintenance-view',
    defaultValue: 'table'
  });

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'overview',
        label: t`Overview`,
        icon: <IconDashboard />,
        content: (
          <Stack gap='xs'>
            <OverviewKpis />
            <Paper withBorder p={4}>
              <FleetMapView height={420} />
            </Paper>
            <Title order={5}>{t`Open Alerts`}</Title>
            <AlertTable openOnly tableName='fleet-overview-alerts' />
          </Stack>
        )
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        content: <AlertTable />
      },
      {
        name: 'pipeline',
        label: t`Pipeline`,
        icon: <IconTimeline />,
        content: (
          <Stack gap='xs'>
            <PipelineKpis />
            <DeploymentTable mode='pipeline' selection='trip' />
          </Stack>
        )
      },
      {
        name: 'deployments',
        label: t`Deployments`,
        icon: <IconAnchor />,
        content: <DeploymentTable mode='active' />
      },
      SegmentedControlPanel({
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        selection: maintenanceView,
        onChange: setMaintenanceView,
        options: [
          {
            value: 'table',
            label: t`Table View`,
            icon: <IconTable />,
            content: (
              <Stack gap='xs'>
                <MaintenanceKpis />
                <TaskTable selection='trip' />
              </Stack>
            )
          },
          {
            value: 'calendar',
            label: t`Calendar View`,
            icon: <IconCalendar />,
            content: <TaskCalendar />
          }
        ]
      }),
      {
        name: 'trips',
        label: t`Trips`,
        icon: <IconSailboat />,
        content: <TripTable />
      },
      {
        name: 'calendar',
        label: t`Calendar`,
        icon: <IconCalendarEvent />,
        content: <FleetCalendar />
      },
      {
        name: 'sites',
        label: t`Sites`,
        icon: <IconMapPin />,
        content: <SiteTable />
      },
      {
        name: 'device-types',
        label: t`Device Types`,
        icon: <IconRadar />,
        content: <DeviceTypeTable />
      }
    ],
    [maintenanceView, setMaintenanceView]
  );

  if (!user.isLoggedIn() || !user.hasViewRole(UserRoles.fleet)) {
    return <PermissionDenied />;
  }

  return (
    <Stack>
      <PageDetail title={t`Fleet`} actions={[]} />
      <PanelGroup
        pageKey='fleet-index'
        panels={panels}
        pluginPanelWithoutId
        pluginPanelKey={PluginPanelKey.fleet}
      />
    </Stack>
  );
}
