import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Menu,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconChecklist,
  IconDots,
  IconInfoCircle,
  IconListDetails,
  IconPencil,
  IconPlayerPlay,
  IconTool,
  IconTrash,
  IconX
} from '@tabler/icons-react';
import { type ReactNode, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { PanelType } from '@lib/types/Panel';
import { PrintingActions } from '../../components/buttons/PrintingActions';
import InstanceDetail from '../../components/nav/InstanceDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { ChecklistEditor } from '../../fleet/components/ChecklistEditor';
import { taskTypeLabel } from '../../fleet/components/FleetBadges';
import { TaskExecution } from '../../fleet/components/TaskExecution';
import {
  detailFieldsByName,
  taskDetailFields
} from '../../fleet/details/FleetDetailFields';
import { useTaskActions } from '../../fleet/hooks/TaskActions';
import { VerifyResult } from '../../fleet/panels/DeploymentHealthPanel';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { MaintenanceActionTable } from '../../fleet/tables/MaintenanceActionTable';
import { useInstance } from '../../hooks/UseInstance';
import { PortalDetailFields } from '../components/PortalDetails';
import { PortalPage } from '../components/PortalPage';
import { PortalTabs } from '../components/PortalTabs';
import { mainUiUrl } from '../navigation';

/**
 * Maintenance task screen (technician). A started task shows the field flow
 * (TaskExecution): checklist -> actions (replace, remove, add, consume,
 * reposition, destroy, with barcode scan in the stock fields) -> Verify data
 * -> Complete. Photos are added in the Attachments tab.
 */
export default function TaskDetail(): ReactNode {
  const { id } = useParams();
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

  const taskActions = useTaskActions({
    task: task,
    onUpdate: refreshInstance,
    onStarted: () => navigate({ search: '?tab=work' }, { replace: true }),
    onDeleted: () => navigate('/my')
  });

  const state = taskActions.state;
  const isClosed = !!task.pk && !state.isOpen;

  const details = useMemo(() => {
    const fields = detailFieldsByName(taskDetailFields(task));

    return (
      <Stack gap='sm'>
        {task.verification_override_reason && (
          <Alert color='orange' icon={<IconAlertTriangle />}>
            {t`Closed without a passed data check`}:{' '}
            {task.verification_override_reason}
          </Alert>
        )}
        {state.isProposed && (
          <Alert color='blue' icon={<IconInfoCircle />}>
            {t`Start the task to fill in the checklist and record the work.`}
          </Alert>
        )}
        <Paper withBorder p='sm'>
          <SimpleGrid cols={{ base: 2, sm: 3 }} spacing='sm'>
            <PortalDetailFields
              item={task}
              fields={[
                fields.task_type,
                fields.description,
                {
                  ...fields.device,
                  value_formatter: () =>
                    task.device ? (
                      <Anchor
                        size='sm'
                        href={mainUiUrl(
                          getDetailUrl(ModelType.stockitem, task.device)
                        )}
                      >
                        {task.device_detail
                          ? `${task.device_detail.part_name} #${task.device_detail.serial}`
                          : task.device}
                      </Anchor>
                    ) : undefined
                },
                {
                  ...fields.deployment,
                  value_formatter: () =>
                    task.deployment ? (
                      <Anchor
                        size='sm'
                        onClick={() =>
                          navigate(`/deployment/${task.deployment}`)
                        }
                      >
                        {task.deployment_detail?.reference ?? task.deployment}
                      </Anchor>
                    ) : undefined
                },
                {
                  ...fields.site,
                  value_formatter: () =>
                    task.site ? (
                      <Anchor
                        size='sm'
                        onClick={() => navigate(`/sites/${task.site}`)}
                      >
                        {task.site_detail?.name ?? task.site}
                      </Anchor>
                    ) : undefined
                },
                {
                  ...fields.trip,
                  value_formatter: () =>
                    task.trip ? (
                      <Anchor
                        size='sm'
                        onClick={() => navigate(`/trips/${task.trip}`)}
                      >
                        {task.trip_reference ?? task.trip}
                      </Anchor>
                    ) : undefined
                },
                fields.due_date,
                fields.scheduled_date,
                fields.started_at,
                fields.completed_at,
                fields.completed_by,
                fields.technicians,
                fields.labour_minutes,
                fields.as_found,
                fields.summary
              ]}
            />
          </SimpleGrid>
        </Paper>
      </Stack>
    );
  }, [task, state, navigate]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'work',
        label: t`Work`,
        icon: <IconTool />,
        hidden: !state.inProgress,
        content: task.pk ? (
          <TaskExecution task={task} onUpdate={refreshInstance} />
        ) : null
      },
      {
        name: 'details',
        label: t`Details`,
        icon: <IconInfoCircle />,
        content: details
      },
      {
        name: 'checklist',
        label: t`Checklist`,
        icon: <IconChecklist />,
        hidden: !isClosed,
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
        hidden: !isClosed,
        content: task.pk ? (
          <MaintenanceActionTable
            taskId={task.pk}
            tableName='fleet-portal-task-actions'
          />
        ) : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        hidden: !task.alerts?.length,
        content: task.pk ? (
          <AlertTable
            params={{ task: task.pk }}
            tableName='fleet-portal-task-alerts'
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
    [task, state, isClosed, details, refreshInstance]
  );

  const actions = [
    state.canStart ? (
      <Button
        key='start'
        color='green'
        size='md'
        leftSection={<IconPlayerPlay size={20} />}
        onClick={taskActions.openStart}
      >
        {t`Start Task`}
      </Button>
    ) : null,
    task.pk ? (
      <PrintingActions
        key='print'
        modelType={ModelType.maintenancetask}
        items={[task.pk]}
        enableReports
      />
    ) : null,
    state.canEdit || state.canCancel || state.canDelete ? (
      <Menu key='options' position='bottom-end'>
        <Menu.Target>
          <Button
            variant='subtle'
            leftSection={<IconDots size={18} />}
            aria-label='task-options'
          >
            {t`More`}
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          {state.canEdit && (
            <Menu.Item
              leftSection={<IconPencil size={18} />}
              onClick={taskActions.openEdit}
            >
              {t`Edit`}
            </Menu.Item>
          )}
          {state.canCancel && (
            <Menu.Item
              color='orange'
              leftSection={<IconX size={18} />}
              onClick={taskActions.openCancel}
            >
              {t`Cancel Task`}
            </Menu.Item>
          )}
          {state.canDelete && (
            <Menu.Item
              color='red'
              leftSection={<IconTrash size={18} />}
              onClick={taskActions.openDelete}
            >
              {t`Delete`}
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    ) : null
  ];

  const subtitle = [task.display_name, task.description]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {taskActions.modals}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <PortalPage
          title={`${t`Task`} ${task.reference ?? ''}`}
          subtitle={subtitle}
          back='/my'
          badges={[
            <StatusRenderer
              key='status'
              status={task.status_custom_key || task.status}
              type={ModelType.maintenancetask}
              options={{ size: 'lg' }}
            />,
            task.task_type ? (
              <Badge key='type' size='lg' variant='light'>
                {taskTypeLabel(task.task_type)}
              </Badge>
            ) : null,
            task.overdue ? (
              <Badge key='overdue' color='red' size='lg'>
                {t`Overdue`}
              </Badge>
            ) : null
          ]}
          actions={actions}
        >
          {state.inProgress && (
            <Text size='sm' c='dimmed'>
              {t`Checklist, then the actions, then "Verify data" and "Complete".`}
            </Text>
          )}
          <PortalTabs
            panels={panels}
            defaultTab={state.inProgress ? 'work' : 'details'}
          />
        </PortalPage>
      </InstanceDetail>
    </>
  );
}
