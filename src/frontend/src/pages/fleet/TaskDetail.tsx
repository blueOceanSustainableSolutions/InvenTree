import { t } from '@lingui/core/macro';
import { Alert, Badge, Skeleton, Stack, Text } from '@mantine/core';
import {
  IconAlertTriangle,
  IconChecklist,
  IconInfoCircle,
  IconListDetails,
  IconTool
} from '@tabler/icons-react';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { PanelType } from '@lib/types/Panel';
import AdminButton from '../../components/buttons/AdminButton';
import PrimaryActionButton from '../../components/buttons/PrimaryActionButton';
import { PrintingActions } from '../../components/buttons/PrintingActions';
import { DetailsTable } from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import {
  BarcodeActionDropdown,
  CancelItemAction,
  DeleteItemAction,
  EditItemAction,
  OptionsActionDropdown
} from '../../components/items/ActionDropdown';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { ChecklistEditor } from '../../fleet/components/ChecklistEditor';
import { taskTypeLabel } from '../../fleet/components/FleetBadges';
import { TaskExecution } from '../../fleet/components/TaskExecution';
import { taskDetailFields } from '../../fleet/details/FleetDetailFields';
import { useTaskActions, useTaskState } from '../../fleet/hooks/TaskActions';
import { VerifyResult } from '../../fleet/panels/DeploymentHealthPanel';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { MaintenanceActionTable } from '../../fleet/tables/MaintenanceActionTable';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';

/**
 * Detail page for a fleet maintenance task.
 *
 * A started task shows the field flow (TaskExecution): checklist, actions,
 * "Verify data" and closing the task. The service report is printed from the
 * print menu (report template "Fleet Service Report").
 */
export default function TaskDetail() {
  const { id } = useParams();
  const user = useUserState();
  const navigate = useNavigate();

  const {
    instance: task,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_task_list,
    pk: id,
    hasPrimaryKey: true
  });

  const { isProposed, inProgress } = useTaskState(task);

  const detailsPanel = useMemo(() => {
    if (instanceQuery.isFetching && !task.pk) {
      return <Skeleton />;
    }

    const { left, right } = taskDetailFields(task);

    return (
      <Stack gap='xs'>
        {task.verification_override_reason && (
          <Alert color='orange' icon={<IconAlertTriangle />}>
            {t`Closed without a passed data check`}:{' '}
            {task.verification_override_reason}
          </Alert>
        )}
        <ItemDetailsGrid>
          <DetailsTable title={t`Task`} fields={left} item={task} />
          <DetailsTable title={t`Dates and Work`} fields={right} item={task} />
        </ItemDetailsGrid>
      </Stack>
    );
  }, [task, instanceQuery]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'details',
        label: t`Task Details`,
        icon: <IconInfoCircle />,
        content: detailsPanel
      },
      {
        name: 'execution',
        label: t`Execution`,
        icon: <IconTool />,
        hidden: !inProgress,
        content: task.pk ? (
          <TaskExecution task={task} onUpdate={refreshInstance} />
        ) : null
      },
      {
        name: 'checklist',
        label: t`Checklist`,
        icon: <IconChecklist />,
        hidden: inProgress || isProposed,
        content: task.pk ? (
          <Stack gap='xs'>
            <ChecklistEditor taskId={task.pk} editable={false} />
            {task.verification && <VerifyResult result={task.verification} />}
          </Stack>
        ) : null
      },
      {
        name: 'actions',
        label: t`Actions`,
        icon: <IconListDetails />,
        hidden: inProgress || isProposed,
        content: task.pk ? <MaintenanceActionTable taskId={task.pk} /> : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        hidden: !task.alerts?.length,
        content: task.pk ? (
          <AlertTable
            params={{ task: task.pk }}
            tableName='fleet-task-alerts'
          />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.maintenancetask,
        model_id: task.pk
      }),
      NotesPanel({
        model_type: ModelType.maintenancetask,
        model_id: task.pk,
        has_note: !!task.notes
      })
    ],
    [task, detailsPanel, inProgress, isProposed, refreshInstance]
  );

  const taskActions = useTaskActions({
    task: task,
    onUpdate: refreshInstance,
    onDeleted: () => navigate('/fleet/index/maintenance')
  });

  const actions = useMemo(
    () => [
      <PrimaryActionButton
        key='start'
        title={t`Start`}
        icon='issue'
        color='green'
        hidden={!taskActions.state.canStart}
        onClick={taskActions.openStart}
      />,
      <AdminButton
        key='admin'
        model={ModelType.maintenancetask}
        id={task.pk}
      />,
      <BarcodeActionDropdown
        key='barcode'
        model={ModelType.maintenancetask}
        pk={task.pk}
        hash={task?.barcode_hash}
        perm={user.hasChangeRole(UserRoles.fleet)}
      />,
      <PrintingActions
        key='print'
        modelType={ModelType.maintenancetask}
        items={[task.pk]}
        enableReports
      />,
      <OptionsActionDropdown
        key='options'
        tooltip={t`Task Actions`}
        actions={[
          EditItemAction({
            hidden: !taskActions.state.canEdit,
            onClick: taskActions.openEdit
          }),
          CancelItemAction({
            hidden: !taskActions.state.canCancel,
            onClick: taskActions.openCancel
          }),
          DeleteItemAction({
            hidden: !taskActions.state.canDelete,
            onClick: taskActions.openDelete
          })
        ]}
      />
    ],
    [task, taskActions, user]
  );

  const badges = useMemo(() => {
    if (!task.pk) {
      return [];
    }

    return [
      <StatusRenderer
        key='status'
        status={task.status_custom_key || task.status}
        type={ModelType.maintenancetask}
        options={{ size: 'lg' }}
      />,
      task.overdue ? (
        <Badge key='overdue' color='red' size='lg'>
          {t`Overdue`}
        </Badge>
      ) : null
    ].filter(Boolean);
  }, [task]);

  const subtitle = [
    task.display_name,
    task.description,
    task.task_type ? taskTypeLabel(task.task_type) : null
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {taskActions.modals}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <Stack gap='xs'>
          <PageDetail
            title={`${t`Maintenance Task`}: ${task.reference ?? ''}`}
            subtitle={subtitle}
            badges={badges}
            breadcrumbs={[
              { name: t`Fleet`, url: '/fleet/' },
              { name: t`Maintenance`, url: '/fleet/index/maintenance' }
            ]}
            lastCrumb={[
              {
                name: task.reference,
                url: getDetailUrl(ModelType.maintenancetask, task.pk)
              }
            ]}
            actions={actions}
            editAction={taskActions.openEdit}
            editEnabled={taskActions.state.canEdit}
          />
          {isProposed && task.pk && (
            <Text size='sm' c='dimmed'>
              {t`Start the task to fill in the checklist and record the work.`}
            </Text>
          )}
          <PanelGroup
            pageKey='fleet-task'
            panels={panels}
            instance={task}
            reloadInstance={refreshInstance}
            model={ModelType.maintenancetask}
            id={task.pk}
          />
        </Stack>
      </InstanceDetail>
    </>
  );
}
