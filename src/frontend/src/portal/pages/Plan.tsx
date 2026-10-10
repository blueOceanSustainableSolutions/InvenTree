import { t } from '@lingui/core/macro';
import { Paper, SegmentedControl, Stack, Text } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import type { ReactNode } from 'react';

import { FleetCalendar } from '../../fleet/components/FleetCalendar';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { MaintenanceKpis } from '../../fleet/components/PipelineKpis';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { useGlobalSettingsState } from '../../states/SettingsStates';
import { PortalPage } from '../components/PortalPage';
import { useIsPhone } from '../useIsPhone';

/**
 * Maintenance planning (manager): the open tasks which are not on a trip yet
 * (preventive maintenance proposed within the planning horizon, and
 * corrective tasks), soonest due first, each shown by its site nickname or
 * device serial, next to the map. Tick the tasks to group and use
 * "Create Field Trip" or "Add to Trip" (no regions: free selection).
 *
 * The calendar view shows deployments, tasks and trips.
 */
export default function Plan(): ReactNode {
  const isPhone = useIsPhone();
  const globalSettings = useGlobalSettingsState();

  const [view, setView] = useLocalStorage<string>({
    key: 'fleet-portal-plan-view',
    defaultValue: 'list'
  });

  const horizon = globalSettings.getSetting('FLEET_PLAN_HORIZON_DAYS', '45');

  return (
    <PortalPage
      title={t`Maintenance Plan`}
      actions={[
        <SegmentedControl
          key='view'
          value={view}
          onChange={setView}
          data={[
            { value: 'list', label: t`Tasks` },
            { value: 'calendar', label: t`Calendar` }
          ]}
        />
      ]}
    >
      {view == 'calendar' ? (
        <FleetCalendar />
      ) : (
        <Stack gap='sm'>
          <MaintenanceKpis />
          <Paper withBorder p={4}>
            <FleetMapView height={isPhone ? 240 : 320} />
          </Paper>
          <Text size='sm' c='dimmed'>
            {t`Open tasks without a trip, soonest due first. Preventive maintenance is proposed ${horizon} days ahead. Tick the tasks to group into a field trip.`}
          </Text>
          <TaskTable
            params={{ open: true, has_trip: false, ordering: 'due_date' }}
            selection='trip'
            tableName='fleet-portal-plan-tasks'
          />
        </Stack>
      )}
    </PortalPage>
  );
}
