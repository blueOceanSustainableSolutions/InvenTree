import { t } from '@lingui/core/macro';
import { Group, Paper, Skeleton, Stack, Text, Title } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import { IconCalendar, IconTable } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { type ReactNode, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import SegmentedIconControl from '../components/buttons/SegmentedIconControl';
import { useApi } from '../contexts/ApiContext';
import useStatusCodes from '../hooks/UseStatusCodes';
import { CardList, TaskCard, TripCard } from '../portal/components/PortalCards';
import { useIsPhone } from '../portal/useIsPhone';
import { useGlobalSettingsState } from '../states/SettingsStates';
import { useUserState } from '../states/UserState';
import { type FleetSectionInfo, fleetSectionList } from './FleetSectionList';
import { FleetCalendar } from './components/FleetCalendar';
import { FleetMapView } from './components/FleetMap';
import { OverviewKpis } from './components/OverviewKpis';
import { MaintenanceKpis, PipelineKpis } from './components/PipelineKpis';
import { TaskCalendar } from './components/TaskCalendar';
import { AlertTable } from './tables/AlertTable';
import { DeploymentTable } from './tables/DeploymentTable';
import { DeviceTypeTable } from './tables/DeviceTypeTable';
import { PlanTable } from './tables/PlanTable';
import { SiteTable } from './tables/SiteTable';
import { TaskTable } from './tables/TaskTable';
import { TripTable } from './tables/TripTable';

/** A fleet section (FleetSectionList.tsx) with its content */
export type FleetSection = FleetSectionInfo & { content: ReactNode };

/** KPI tiles, map of the deployed devices and the open alerts */
function OverviewSection(): ReactNode {
  const isPhone = useIsPhone();

  return (
    <Stack gap='xs'>
      <OverviewKpis />
      <Paper withBorder p={4}>
        <FleetMapView height={isPhone ? 280 : 420} />
      </Paper>
      <Title order={5}>{t`Open Alerts`}</Title>
      <AlertTable openOnly tableName='fleet-overview-alerts' />
    </Stack>
  );
}

/** Is a trip happening today (started, or today within its dates)? */
function isCurrentTrip(trip: any, today: string, inProgress: number): boolean {
  if (trip.status == inProgress) {
    return true;
  }

  return !trip.start_date || trip.start_date <= today;
}

/**
 * Technician home: my trips (today, then upcoming) and the open tasks
 * assigned to me (as technician, or on a trip where I am in the team or
 * responsible). Tasks in progress come first. Cards are large touch targets.
 */
function MyWorkSection(): ReactNode {
  const api = useApi();
  const user = useUserState();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });
  const tripStatus = useStatusCodes({ modelType: ModelType.fieldtrip });

  const tripsQuery = useQuery({
    queryKey: ['fleet-my-trips', user.userId()],
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_trip_list), {
          params: { mine: true, open: true, ordering: 'start_date' }
        })
        .then((response) => response.data ?? [])
  });

  const tasksQuery = useQuery({
    queryKey: ['fleet-my-tasks', user.userId()],
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_list), {
          params: { assigned_to_me: true, open: true, ordering: 'due_date' }
        })
        .then((response) => response.data ?? [])
  });

  const today = dayjs().format('YYYY-MM-DD');

  const [currentTrips, upcomingTrips] = useMemo(() => {
    const trips: any[] = tripsQuery.data ?? [];

    return [
      trips.filter((trip) =>
        isCurrentTrip(trip, today, tripStatus.IN_PROGRESS)
      ),
      trips.filter(
        (trip) => !isCurrentTrip(trip, today, tripStatus.IN_PROGRESS)
      )
    ];
  }, [tripsQuery.data, today, tripStatus]);

  const tasks: any[] = useMemo(() => {
    const result: any[] = [...(tasksQuery.data ?? [])];

    // Work in progress first; the API order (due date) is kept otherwise
    return result.sort(
      (a, b) =>
        Number(b.status == taskStatus.IN_PROGRESS) -
        Number(a.status == taskStatus.IN_PROGRESS)
    );
  }, [tasksQuery.data, taskStatus]);

  if (tripsQuery.isLoading || tasksQuery.isLoading) {
    return <Skeleton h={200} />;
  }

  return (
    <Stack gap='lg'>
      <CardList title={t`Trips Today`} empty={t`No trip today.`}>
        {currentTrips.map((trip: any) => (
          <TripCard key={trip.pk} trip={trip} />
        ))}
      </CardList>
      <CardList title={t`My Tasks`} empty={t`No open tasks assigned to you.`}>
        {tasks.map((task: any) => (
          <TaskCard key={task.pk} task={task} />
        ))}
      </CardList>
      <CardList title={t`Upcoming Trips`} empty={t`No upcoming trips.`}>
        {upcomingTrips.map((trip: any) => (
          <TripCard key={trip.pk} trip={trip} />
        ))}
      </CardList>
    </Stack>
  );
}

/**
 * The fleet plan: one list of the deployments to do (devices which are not
 * deployed: in production, built, or on their way back) and the deployed
 * devices to maintain, marked Deployment or Maintenance.
 *
 * Order: the next planning-horizon days (overdue first) by date, then the
 * rows without a date, then the later dates. Tick rows to group them into a
 * field trip ("Create Field Trip" / "Add to Trip").
 */
function PlanSection(): ReactNode {
  const isPhone = useIsPhone();
  const globalSettings = useGlobalSettingsState();

  const horizon = globalSettings.getSetting('FLEET_PLAN_HORIZON_DAYS', '45');

  return (
    <Stack gap='xs'>
      <PipelineKpis />
      <Paper withBorder p={4}>
        <FleetMapView height={isPhone ? 240 : 320} />
      </Paper>
      <Text size='sm' c='dimmed'>
        {t`Deployments and maintenance: the next ${horizon} days first, then the rows without a date, then the later dates. Tick rows to group them into a field trip.`}
      </Text>
      <PlanTable />
    </Stack>
  );
}

/** Maintenance tasks, as a table (tick tasks to group a trip) or a calendar */
function MaintenanceSection(): ReactNode {
  const [view, setView] = useLocalStorage<string>({
    key: 'fleet-maintenance-view',
    defaultValue: 'table'
  });

  return (
    <Stack gap='xs'>
      <Group justify='flex-end'>
        <SegmentedIconControl
          value={view}
          onChange={setView}
          data={[
            { value: 'table', label: t`Table View`, icon: <IconTable /> },
            {
              value: 'calendar',
              label: t`Calendar View`,
              icon: <IconCalendar />
            }
          ]}
        />
      </Group>
      {view == 'calendar' ? (
        <TaskCalendar />
      ) : (
        <>
          <MaintenanceKpis />
          <TaskTable selection='trip' />
        </>
      )}
    </Stack>
  );
}

/** The content of each section, by section name */
function sectionContent(name: string): ReactNode {
  switch (name) {
    case 'overview':
      return <OverviewSection />;
    case 'my-work':
      return <MyWorkSection />;
    case 'alerts':
      return <AlertTable />;
    case 'plan':
      return <PlanSection />;
    case 'deployments':
      return <DeploymentTable mode='active' />;
    case 'maintenance':
      return <MaintenanceSection />;
    case 'trips':
      return <TripTable />;
    case 'calendar':
      return <FleetCalendar />;
    case 'sites':
      return <SiteTable />;
    case 'device-types':
      return <DeviceTypeTable />;
    default:
      return null;
  }
}

/** The sections of the fleet module with their content, in navigation order */
export function fleetSections(): FleetSection[] {
  return fleetSectionList().map((section) => ({
    ...section,
    content: sectionContent(section.name)
  }));
}
