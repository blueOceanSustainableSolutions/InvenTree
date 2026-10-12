import { t } from '@lingui/core/macro';
import { Paper, SegmentedControl, Stack, Text } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import type { ReactNode } from 'react';

import { FleetCalendar } from '../../fleet/components/FleetCalendar';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { PipelineKpis } from '../../fleet/components/PipelineKpis';
import { PlanTable } from '../../fleet/tables/PlanTable';
import { useGlobalSettingsState } from '../../states/SettingsStates';
import { PortalPage } from '../components/PortalPage';
import { useIsPhone } from '../useIsPhone';

/**
 * The fleet plan (manager): one list of the deployments to do (devices which
 * are not deployed: in production, built, or on their way back) and the
 * deployed devices to maintain, marked Deployment or Maintenance.
 *
 * Order: the next planning-horizon days (overdue first) by date, then the
 * rows without a date, then the later dates. Tick rows to group them into a
 * field trip ("Create Field Trip" / "Add to Trip"); "New Deployment" plans
 * where and when an undeployed device goes.
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
      title={t`Fleet Plan`}
      actions={[
        <SegmentedControl
          key='view'
          value={view}
          onChange={setView}
          data={[
            { value: 'list', label: t`List` },
            { value: 'calendar', label: t`Calendar` }
          ]}
        />
      ]}
    >
      {view == 'calendar' ? (
        <FleetCalendar />
      ) : (
        <Stack gap='sm'>
          <PipelineKpis />
          <Paper withBorder p={4}>
            <FleetMapView height={isPhone ? 240 : 320} />
          </Paper>
          <Text size='sm' c='dimmed'>
            {t`Deployments and maintenance: the next ${horizon} days first, then the rows without a date, then the later dates. Tick rows to group them into a field trip.`}
          </Text>
          <PlanTable tableName='fleet-portal-plan' />
        </Stack>
      )}
    </PortalPage>
  );
}
