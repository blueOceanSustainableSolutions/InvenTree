import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Menu,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconArrowBackUp,
  IconDots,
  IconInfoCircle,
  IconListCheck,
  IconPackages,
  IconPencil,
  IconPlayerPlay,
  IconPlus,
  IconTrash,
  IconX
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { PanelType } from '@lib/types/Panel';
import { PrintingActions } from '../../components/buttons/PrintingActions';
import InstanceDetail from '../../components/nav/InstanceDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { TripStatusSteps } from '../../fleet/components/TripStatusSteps';
import {
  detailFieldsByName,
  tripDetailFields
} from '../../fleet/details/FleetDetailFields';
import { useTripActions } from '../../fleet/hooks/TripActions';
import { TripReconcilePanel } from '../../fleet/panels/TripReconcilePanel';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { KitStockTable, TripKitTable } from '../../fleet/tables/TripKitTable';
import { useInstance } from '../../hooks/UseInstance';
import { CardList, TaskCard } from '../components/PortalCards';
import { PortalDetailFields } from '../components/PortalDetails';
import { PortalPage } from '../components/PortalPage';
import { PortalTabs } from '../components/PortalTabs';

/** The tasks of a trip, one group of cards per site (nickname or serial) */
function TasksBySite({ tripId }: Readonly<{ tripId: number }>): ReactNode {
  const api = useApi();

  const query = useQuery({
    queryKey: ['fleet-portal-trip-tasks', tripId],
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_list), {
          params: { trip: tripId, ordering: 'site' }
        })
        .then((response) => response.data ?? [])
  });

  const groups = useMemo(() => {
    const result: Record<string, any[]> = {};

    for (const task of query.data ?? []) {
      const key = task.display_name || t`No site`;
      result[key] = [...(result[key] ?? []), task];
    }

    return Object.entries(result).sort(([a], [b]) => a.localeCompare(b));
  }, [query.data]);

  if (query.isLoading) {
    return <Skeleton h={120} />;
  }

  if (groups.length == 0) {
    return <Text c='dimmed'>{t`No tasks on this trip yet.`}</Text>;
  }

  return (
    <Stack gap='md'>
      {groups.map(([site, tasks]) => (
        <CardList key={site} title={site} empty=''>
          {tasks.map((task: any) => (
            <TaskCard key={task.pk} task={task} />
          ))}
        </CardList>
      ))}
    </Stack>
  );
}

/**
 * Field trip screen: header with the status steps (Planning -> Kit ready ->
 * In progress -> Reconciling -> Closed), tasks by site, the kit (suggested,
 * available, taken; prepare), reconcile (leftovers back to stock, removed
 * parts to the workshop), files, notes and the trip report.
 */
export default function TripDetail(): ReactNode {
  const { id } = useParams();
  const navigate = useNavigate();

  const [taskView, setTaskView] = useState<string>('site');

  const {
    instance: trip,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_trip_list,
    pk: id,
    hasPrimaryKey: true
  });

  const { instance: kit, refreshInstance: refreshKit } = useInstance({
    endpoint: ApiEndpoints.fleet_trip_kit,
    pk: id,
    hasPrimaryKey: true,
    defaultValue: {}
  });

  const refreshAll = useCallback(() => {
    refreshInstance();
    refreshKit();
  }, [refreshInstance, refreshKit]);

  const tripActions = useTripActions({
    trip: trip,
    kit: kit,
    onUpdate: refreshAll,
    onDeleted: () => navigate('/trips')
  });

  const state = tripActions.state;

  const panels: PanelType[] = useMemo(() => {
    const fields = detailFieldsByName(tripDetailFields(trip));

    return [
      {
        name: 'tasks',
        label: t`Tasks`,
        icon: <IconListCheck />,
        content: trip.pk ? (
          <Stack gap='sm'>
            <SegmentedControl
              value={taskView}
              onChange={setTaskView}
              data={[
                { value: 'site', label: t`By Site` },
                { value: 'table', label: t`Table` }
              ]}
            />
            {taskView == 'site' ? (
              <TasksBySite
                key={`${trip.task_count}-${trip.open_task_count}`}
                tripId={trip.pk}
              />
            ) : (
              <TaskTable
                params={{ trip: trip.pk }}
                tableName='fleet-portal-trip-tasks'
                tripId={trip.pk}
                tripEditable={state.plannable}
                onTripChange={refreshAll}
              />
            )}
          </Stack>
        ) : null
      },
      {
        name: 'kit',
        label: t`Kit`,
        icon: <IconPackages />,
        content: trip.pk ? (
          <Stack gap='sm'>
            {!kit?.kit_location && (
              <Alert color='blue'>{t`The trip has no kit location yet.`}</Alert>
            )}
            <TripKitTable
              trip={trip}
              lines={kit?.lines ?? []}
              editable={state.plannable}
              canTake={state.canTake}
              onChange={refreshAll}
            />
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Kit Contents`}</Title>
                <KitStockTable
                  items={kit?.contents ?? []}
                  tableName='fleet-portal-trip-kit-contents'
                />
              </Stack>
            </Paper>
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Removed Components`}</Title>
                <KitStockTable
                  items={kit?.removed ?? []}
                  tableName='fleet-portal-trip-kit-removed'
                />
              </Stack>
            </Paper>
          </Stack>
        ) : null
      },
      {
        name: 'reconcile',
        label: t`Reconcile`,
        icon: <IconArrowBackUp />,
        hidden: state.isPlanning || state.isCancelled,
        content: trip.pk ? (
          <TripReconcilePanel
            trip={trip}
            kit={kit}
            canReconcile={state.canReconcile}
            onChange={refreshAll}
          />
        ) : null
      },
      {
        name: 'details',
        label: t`Details`,
        icon: <IconInfoCircle />,
        content: (
          <Paper withBorder p='sm'>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='xs'>
              <PortalDetailFields
                item={trip}
                fields={[
                  fields.title,
                  fields.vessel,
                  {
                    type: 'string',
                    name: 'dates',
                    label: t`Dates`,
                    value_formatter: () =>
                      [trip.start_date, trip.end_date]
                        .filter(Boolean)
                        .map((date: string) => formatDate(date))
                        .join(' - ')
                  },
                  fields.responsible,
                  fields.team,
                  fields.task_count,
                  {
                    ...fields.kit_location,
                    value_formatter: () => trip.kit_location_name
                  }
                ]}
              />
            </SimpleGrid>
          </Paper>
        )
      },
      AttachmentPanel({
        model_type: ModelType.fieldtrip,
        model_id: trip.pk
      }),
      NotesPanel({
        model_type: ModelType.fieldtrip,
        model_id: trip.pk,
        has_note: !!trip.notes
      })
    ];
  }, [trip, kit, state, taskView, refreshAll]);

  const actions = [
    state.canStart ? (
      <Button
        key='start'
        color='green'
        leftSection={<IconPlayerPlay size={18} />}
        onClick={tripActions.openStart}
      >
        {t`Start Trip`}
      </Button>
    ) : null,
    state.canAddTasks ? (
      <Button
        key='add-tasks'
        variant='outline'
        leftSection={<IconPlus size={18} />}
        onClick={tripActions.openAddTasks}
      >
        {t`Add Tasks`}
      </Button>
    ) : null,
    trip.pk ? (
      <PrintingActions
        key='print'
        modelType={ModelType.fieldtrip}
        items={[trip.pk]}
        enableReports
      />
    ) : null,
    state.canEdit || state.canCancel || state.canDelete ? (
      <Menu key='options' position='bottom-end'>
        <Menu.Target>
          <Button
            variant='subtle'
            leftSection={<IconDots size={18} />}
            aria-label='trip-options'
          >
            {t`More`}
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          {state.canEdit && (
            <Menu.Item
              leftSection={<IconPencil size={18} />}
              onClick={tripActions.openEdit}
            >
              {t`Edit`}
            </Menu.Item>
          )}
          {state.canCancel && (
            <Menu.Item
              color='orange'
              leftSection={<IconX size={18} />}
              onClick={tripActions.openCancel}
            >
              {t`Cancel Trip`}
            </Menu.Item>
          )}
          {state.canDelete && (
            <Menu.Item
              color='red'
              leftSection={<IconTrash size={18} />}
              onClick={tripActions.openDelete}
            >
              {t`Delete`}
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    ) : null
  ];

  return (
    <>
      {tripActions.modals}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <PortalPage
          title={`${t`Field Trip`} ${trip.reference ?? ''}`}
          subtitle={[trip.title, trip.vessel].filter(Boolean).join(' · ')}
          back='/trips'
          badges={[
            <StatusRenderer
              key='status'
              status={trip.status_custom_key || trip.status}
              type={ModelType.fieldtrip}
              options={{ size: 'lg' }}
            />
          ]}
          actions={actions}
        >
          {trip.pk && (
            <Paper withBorder p='sm' style={{ overflowX: 'auto' }}>
              <TripStatusSteps status={trip.status} />
            </Paper>
          )}
          {state.isClosed && (
            <Text size='sm' c='dimmed'>
              {t`This trip is closed: the kit was returned to stock.`}
            </Text>
          )}
          <PortalTabs panels={panels} />
        </PortalPage>
      </InstanceDetail>
    </>
  );
}
