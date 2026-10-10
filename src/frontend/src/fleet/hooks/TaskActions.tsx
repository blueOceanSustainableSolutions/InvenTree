import { t } from '@lingui/core/macro';
import { Alert } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { type ReactNode, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { cancelTaskFields, useTaskFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';

/** What a task is, and which actions the user may run on it */
export type TaskState = {
  isProposed: boolean;
  inProgress: boolean;
  isOpen: boolean;
  canStart: boolean;
  canEdit: boolean;
  canCancel: boolean;
  canDelete: boolean;
};

/**
 * The state of a maintenance task and the actions allowed to the user.
 * `isProposed` covers proposed and scheduled tasks (not started yet).
 */
export function useTaskState(task: any): TaskState {
  const user = useUserState();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });

  return useMemo(() => {
    const status = task?.status;
    const isProposed =
      status == taskStatus.PROPOSED || status == taskStatus.SCHEDULED;
    const inProgress = status == taskStatus.IN_PROGRESS;
    const isOpen = isProposed || inProgress;
    const canChange = !!task?.pk && user.hasChangeRole(UserRoles.fleet);

    return {
      isProposed,
      inProgress,
      isOpen,
      canStart: isProposed && canChange,
      canEdit: isOpen && canChange,
      canCancel: isOpen && canChange,
      canDelete:
        !!task?.pk &&
        user.hasDeleteRole(UserRoles.fleet) &&
        (status == taskStatus.PROPOSED || status == taskStatus.CANCELLED) &&
        !(task?.action_count > 0)
    };
  }, [task, user, taskStatus]);
}

/**
 * The action forms of a maintenance task, shared by the task page of the
 * main UI and the Fleet Portal: start, edit, cancel and delete.
 *
 * Render `modals` once; call the `open*` functions from buttons.
 */
export function useTaskActions({
  task,
  onUpdate,
  onStarted,
  onDeleted
}: {
  task: any;
  onUpdate: () => void;
  onStarted?: () => void;
  onDeleted?: () => void;
}) {
  const state = useTaskState(task);

  const editFields = useTaskFields({ create: false });

  const editTask = useEditApiFormModal({
    url: ApiEndpoints.fleet_task_list,
    pk: task?.pk,
    title: t`Edit Task`,
    fields: editFields,
    onFormSuccess: onUpdate
  });

  const deleteTask = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_task_list,
    pk: task?.pk,
    title: t`Delete Task`,
    onFormSuccess: () => onDeleted?.()
  });

  const startTask = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_start, task?.pk),
    title: t`Start Task`,
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {t`Start the work on this device. The checklist of its device type is added to the task.`}
      </Alert>
    ),
    successMessage: t`Task started`,
    onFormSuccess: () => {
      onUpdate();
      onStarted?.();
    }
  });

  const cancelTask = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_cancel, task?.pk),
    title: t`Cancel Task`,
    fields: cancelTaskFields(),
    preFormWarning: t`Stock changes made during the task are kept.`,
    successMessage: t`Task cancelled`,
    onFormSuccess: onUpdate
  });

  const modals: ReactNode = (
    <>
      {editTask.modal}
      {deleteTask.modal}
      {startTask.modal}
      {cancelTask.modal}
    </>
  );

  return {
    state,
    modals,
    openStart: startTask.open,
    openEdit: editTask.open,
    openCancel: cancelTask.open,
    openDelete: deleteTask.open
  };
}
