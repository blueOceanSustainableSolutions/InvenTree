import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Modal, Stack, Tabs, Text } from '@mantine/core';
import { showNotification } from '@mantine/notifications';
import { IconAnchor, IconInfoCircle, IconTool } from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useRef, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import { useApi } from '../../contexts/ApiContext';
import { cancelTripFields, useTripFields } from '../../forms/FleetForms';
import { showApiErrorMessage } from '../../functions/notifications';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import {
  createTeamErrorStore,
  teamErrorMessage
} from '../components/TeamSelect';
import { DeploymentTable } from '../tables/DeploymentTable';
import { TaskTable } from '../tables/TaskTable';

/** What a trip is, and which actions the user may run on it */
export type TripState = {
  isPlanning: boolean;
  isKitReady: boolean;
  inProgress: boolean;
  isReconciling: boolean;
  isClosed: boolean;
  isCancelled: boolean;
  /** Tasks can be added and the kit changed (until reconciling) */
  plannable: boolean;
  isOpen: boolean;
  canChange: boolean;
  canStart: boolean;
  canEdit: boolean;
  canCancel: boolean;
  canDelete: boolean;
  canAddTasks: boolean;
  /** Take stock into the kit (also needs stock change) */
  canTake: boolean;
  canReconcile: boolean;
};

/**
 * The state of a field trip and the actions allowed to the user
 */
export function useTripState(trip: any): TripState {
  const user = useUserState();
  const tripStatus = useStatusCodes({ modelType: ModelType.fieldtrip });

  return useMemo(() => {
    const status = trip?.status;
    const isPlanning = status == tripStatus.PLANNING;
    const isKitReady = status == tripStatus.KIT_READY;
    const inProgress = status == tripStatus.IN_PROGRESS;
    const isReconciling = status == tripStatus.RECONCILING;
    const isClosed = status == tripStatus.CLOSED;
    const isCancelled = status == tripStatus.CANCELLED;

    const plannable = isPlanning || isKitReady || inProgress;
    const isOpen = plannable || isReconciling;
    const canChange = !!trip?.pk && user.hasChangeRole(UserRoles.fleet);

    return {
      isPlanning,
      isKitReady,
      inProgress,
      isReconciling,
      isClosed,
      isCancelled,
      plannable,
      isOpen,
      canChange,
      canStart: (isPlanning || isKitReady) && canChange,
      canEdit: isOpen && canChange,
      canCancel: (isPlanning || isKitReady) && canChange,
      canDelete:
        !!trip?.pk &&
        user.hasDeleteRole(UserRoles.fleet) &&
        (isPlanning || isCancelled) &&
        (trip?.open_task_count ?? 0) == 0,
      canAddTasks: plannable && canChange,
      canTake: plannable && canChange && user.hasChangeRole(UserRoles.stock),
      canReconcile: isKitReady || inProgress || isReconciling
    };
  }, [trip, user, tripStatus]);
}

/**
 * Picker to add open tasks (without a trip) and ready pipeline deployments
 * to a trip. A ready deployment gets a deployment (or swap) task.
 */
export function AddTasksModal({
  trip,
  opened,
  onClose,
  onAdded
}: Readonly<{
  trip: any;
  opened: boolean;
  onClose: () => void;
  onAdded: () => void;
}>): ReactNode {
  const api = useApi();
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });

  const taskTable = useTable('fleet-trip-pick-tasks');
  const deploymentTable = useTable('fleet-trip-pick-deployments');

  const [saving, setSaving] = useState<boolean>(false);

  const taskIds: number[] = taskTable.selectedIds;
  const deploymentIds: number[] = deploymentTable.selectedIds;

  const close = useCallback(() => {
    taskTable.clearSelectedRecords();
    deploymentTable.clearSelectedRecords();
    onClose();
  }, [taskTable, deploymentTable, onClose]);

  const submit = useCallback(() => {
    setSaving(true);

    api
      .post(apiUrl(ApiEndpoints.fleet_trip_add_tasks, trip.pk), {
        tasks: taskIds,
        deployments: deploymentIds
      })
      .then(() => {
        showNotification({
          title: t`Success`,
          message: t`Added to the field trip`,
          color: 'green'
        });
        onAdded();
        close();
      })
      .catch((error) =>
        showApiErrorMessage({ error: error, title: t`Add Tasks` })
      )
      .finally(() => setSaving(false));
  }, [api, trip.pk, taskIds, deploymentIds, onAdded, close]);

  return (
    <Modal opened={opened} onClose={close} title={t`Add Tasks`} size='90%'>
      {opened && (
        <Stack gap='sm'>
          <Tabs defaultValue='tasks'>
            <Tabs.List>
              <Tabs.Tab value='tasks' leftSection={<IconTool size={16} />}>
                {t`Open Tasks`} ({taskIds.length})
              </Tabs.Tab>
              <Tabs.Tab
                value='deployments'
                leftSection={<IconAnchor size={16} />}
              >
                {t`Ready Deployments`} ({deploymentIds.length})
              </Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value='tasks' pt='xs'>
              <TaskTable
                params={{ open: true, has_trip: false }}
                tableName='fleet-trip-pick-tasks'
                selection='picker'
                tableState={taskTable}
              />
            </Tabs.Panel>
            <Tabs.Panel value='deployments' pt='xs'>
              <Text size='sm' c='dimmed'>
                {t`Each ready deployment gets a deployment task (a swap task when it replaces a deployed device); its device is added to the kit.`}
              </Text>
              <DeploymentTable
                mode='pipeline'
                params={{ status: deploymentStatus.READY }}
                tableName='fleet-trip-pick-deployments'
                selection='picker'
                tableState={deploymentTable}
              />
            </Tabs.Panel>
          </Tabs>
          <Group justify='right'>
            <Button variant='outline' onClick={close}>
              {t`Cancel`}
            </Button>
            <Button
              color='green'
              loading={saving}
              disabled={taskIds.length + deploymentIds.length == 0}
              onClick={submit}
            >
              {t`Add to Trip`}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

/**
 * The action forms of a field trip, shared by the trip page of the main UI
 * and the Fleet Portal: start, edit (with the team), cancel, delete and the
 * "Add Tasks" picker.
 *
 * `kit` is the result of `trip/<pk>/kit/` (used to warn about an empty kit).
 * Render `modals` once; call the `open*` functions from buttons.
 */
export function useTripActions({
  trip,
  kit,
  onUpdate,
  onDeleted
}: {
  trip: any;
  kit?: any;
  onUpdate: () => void;
  onDeleted?: () => void;
}) {
  const state = useTripState(trip);

  const [addTasksOpened, setAddTasksOpened] = useState<boolean>(false);
  const team = useRef<number[]>([]);
  const [teamErrors] = useState(createTeamErrorStore);

  const onTeamChange = useCallback((value: number[]) => {
    team.current = value;
  }, []);

  const initialTeam: number[] = useMemo(() => trip?.team ?? [], [trip?.team]);

  const editFields = useTripFields({ initialTeam, onTeamChange, teamErrors });

  const editTrip = useEditApiFormModal({
    url: ApiEndpoints.fleet_trip_list,
    pk: trip?.pk,
    title: t`Edit Field Trip`,
    fields: editFields,
    processFormData: (data: any) => ({ ...data, team: team.current }),
    onFormError: (error: any) => teamErrors.set(teamErrorMessage(error)),
    onFormSuccess: onUpdate
  });

  const openEdit = useCallback(() => {
    team.current = trip?.team ?? [];
    teamErrors.set(undefined);
    editTrip.open();
  }, [trip, editTrip, teamErrors]);

  const deleteTrip = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_trip_list,
    pk: trip?.pk,
    title: t`Delete Field Trip`,
    onFormSuccess: () => onDeleted?.()
  });

  const startTrip = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_start, trip?.pk),
    title: t`Start Trip`,
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {t`The team is leaving for the field. Tasks are started one by one on site.`}
      </Alert>
    ),
    preFormWarning:
      state.isPlanning && (kit?.contents ?? []).length == 0
        ? t`The kit is empty: no stock has been taken yet.`
        : undefined,
    successMessage: t`Trip started`,
    onFormSuccess: onUpdate
  });

  const cancelTrip = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_cancel, trip?.pk),
    title: t`Cancel Field Trip`,
    fields: cancelTripFields(),
    preFormWarning: t`The kit must be empty. The tasks of the trip go back to proposed.`,
    successMessage: t`Trip cancelled`,
    onFormSuccess: onUpdate
  });

  const modals: ReactNode = (
    <>
      {editTrip.modal}
      {deleteTrip.modal}
      {startTrip.modal}
      {cancelTrip.modal}
      {trip?.pk && (
        <AddTasksModal
          trip={trip}
          opened={addTasksOpened}
          onClose={() => setAddTasksOpened(false)}
          onAdded={onUpdate}
        />
      )}
    </>
  );

  return {
    state,
    modals,
    openStart: startTrip.open,
    openEdit,
    openCancel: cancelTrip.open,
    openDelete: deleteTrip.open,
    openAddTasks: () => setAddTasksOpened(true)
  };
}
