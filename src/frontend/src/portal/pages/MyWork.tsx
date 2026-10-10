import { t } from '@lingui/core/macro';
import { Skeleton, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { type ReactNode, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import { CardList, TaskCard, TripCard } from '../components/PortalCards';
import { PortalPage } from '../components/PortalPage';

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
export default function MyWork(): ReactNode {
  const api = useApi();
  const user = useUserState();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });
  const tripStatus = useStatusCodes({ modelType: ModelType.fieldtrip });

  const tripsQuery = useQuery({
    queryKey: ['fleet-portal-my-trips', user.userId()],
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_trip_list), {
          params: { mine: true, open: true, ordering: 'start_date' }
        })
        .then((response) => response.data ?? [])
  });

  const tasksQuery = useQuery({
    queryKey: ['fleet-portal-my-tasks', user.userId()],
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

  return (
    <PortalPage title={t`My Work`} subtitle={user.username()}>
      {tripsQuery.isLoading || tasksQuery.isLoading ? (
        <Skeleton h={200} />
      ) : (
        <Stack gap='lg'>
          <CardList title={t`Trips Today`} empty={t`No trip today.`}>
            {currentTrips.map((trip: any) => (
              <TripCard key={trip.pk} trip={trip} />
            ))}
          </CardList>
          <CardList
            title={t`My Tasks`}
            empty={t`No open tasks assigned to you.`}
          >
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
      )}
    </PortalPage>
  );
}
