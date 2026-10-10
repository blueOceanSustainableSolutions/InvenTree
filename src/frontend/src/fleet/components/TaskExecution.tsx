import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconAnchor,
  IconCircleCheck,
  IconCurrentLocation,
  IconDroplet,
  IconFlame,
  IconPhoto,
  IconRefresh,
  IconReplace,
  IconTool,
  IconUnlink
} from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useState } from 'react';

import { AddItemButton } from '@lib/components/AddItemButton';
import type { RowAction } from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableColumn } from '@lib/types/Tables';
import { useApi } from '../../contexts/ApiContext';
import { useComponentServiceForms } from '../../forms/ComponentServiceForms';
import {
  completeTaskFields,
  consumeFields,
  recordActionFields,
  repositionFields,
  taskComponentFields,
  taskDeployFields,
  taskRemovalFields
} from '../../forms/FleetForms';
import { showApiErrorMessage } from '../../functions/notifications';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useInstance } from '../../hooks/UseInstance';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import {
  PartColumn,
  StatusColumn,
  StockColumn
} from '../../tables/ColumnRenderers';
import { InvenTreeTable } from '../../tables/InvenTreeTable';
import { VerifyResult } from '../panels/DeploymentHealthPanel';
import { MaintenanceActionTable } from '../tables/MaintenanceActionTable';
import { ChecklistEditor } from './ChecklistEditor';
import { StreamStatusList } from './StreamStatusList';

/** Task types which put a device in the water */
const DEPLOY_TASK_TYPES = ['DEPLOYMENT', 'SWAP'];

/**
 * Components installed in the task's device, with the component service
 * actions (replace, remove, destroy, add) recorded against the task
 */
function TaskComponents({
  task,
  canService,
  onChange
}: Readonly<{
  task: any;
  canService: boolean;
  onChange: () => void;
}>): ReactNode {
  const table = useTable('fleet-task-components');
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });
  const stockStatus = useStatusCodes({ modelType: ModelType.stockitem });

  const deployed = task.deployment_detail?.status == deploymentStatus.DEPLOYED;

  const serviceForms = useComponentServiceForms({
    unit: task.device_detail ?? { pk: task.device },
    url: ApiEndpoints.fleet_task_component_action,
    pk: task.pk,
    extraFields: taskComponentFields(task.pk),
    removalFields: taskRemovalFields(deployed ? task.deployment : undefined),
    defaultLocation: null,
    location: {
      description: t`Leave blank for the field trip "Removed" location, or the fleet workshop`
    },
    table: table,
    onSuccess: onChange
  });

  const columns: TableColumn[] = useMemo(
    () => [
      PartColumn({ part: 'part_detail' }),
      StockColumn({
        accessor: '',
        title: t`Stock Item`,
        sortable: false
      }),
      StatusColumn({ model: ModelType.stockitem })
    ],
    []
  );

  const rowActions = useCallback(
    (record: any): RowAction[] => [
      {
        title: t`Replace`,
        tooltip: t`Remove this component and install another stock item`,
        icon: <IconReplace />,
        hidden: !canService,
        onClick: () => serviceForms.openReplace(record)
      },
      {
        title: t`Remove`,
        tooltip: t`Remove this component without a replacement`,
        icon: <IconUnlink />,
        hidden: !canService,
        onClick: () => serviceForms.openRemove(record)
      },
      {
        title: t`Mark Destroyed`,
        tooltip: t`Keep the component installed and mark it as destroyed`,
        icon: <IconFlame />,
        color: 'red',
        hidden: !canService || record.status == stockStatus.DESTROYED,
        onClick: () => serviceForms.openDestroy(record)
      }
    ],
    [canService, serviceForms, stockStatus]
  );

  const tableActions = useMemo(
    () => [
      <AddItemButton
        key='add-component'
        tooltip={t`Add Component`}
        hidden={!canService}
        onClick={() => serviceForms.openInstall()}
      />
    ],
    [canService, serviceForms]
  );

  return (
    <>
      {serviceForms.modals}
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.stock_item_list)}
        tableState={table}
        columns={columns}
        props={{
          params: { belongs_to: task.device, part_detail: true },
          rowActions: rowActions,
          tableActions: tableActions,
          enableSearch: false,
          modelType: ModelType.stockitem
        }}
      />
    </>
  );
}

/**
 * The field flow of a maintenance task, shared by the main UI and the Fleet
 * Portal: live streams, checklist, actions (components, consumables,
 * reposition, notes), "Verify data" and closing the task.
 */
export function TaskExecution({
  task,
  onUpdate
}: Readonly<{
  task: any;
  onUpdate: () => void;
}>): ReactNode {
  const api = useApi();
  const user = useUserState();
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });

  const [verifying, setVerifying] = useState<boolean>(false);
  const [checklistAction, setChecklistAction] = useState<any>({});

  const canChange = user.hasChangeRole(UserRoles.fleet);
  const canService = canChange && user.hasChangeRole(UserRoles.stock);

  // In the water: deployed and not docked (a docked device is on land)
  const deployed =
    task.deployment_detail?.status == deploymentStatus.DEPLOYED &&
    task.deployment_detail?.internal_state != 'DOCKED';
  const verification = task.verification;
  const needsOverride = deployed && !verification?.passed;

  // A deployment or swap task whose device is not in the water yet
  const canDeploy =
    DEPLOY_TASK_TYPES.includes(task.task_type) &&
    // The device is built (ready) or the deployment is scheduled
    [deploymentStatus.READY, deploymentStatus.SCHEDULED].includes(
      task.deployment_detail?.status
    );
  const isSwap = task.task_type == 'SWAP';

  // The deployment (position to pre-fill the deploy form)
  const { instance: deployment } = useInstance({
    endpoint: ApiEndpoints.fleet_deployment_list,
    pk: task.deployment,
    hasPrimaryKey: true,
    disabled: !canDeploy
  });

  // The trip of the task: consumables come from its kit
  const { instance: trip } = useInstance({
    endpoint: ApiEndpoints.fleet_trip_list,
    pk: task.trip,
    hasPrimaryKey: true,
    disabled: !task.trip
  });

  const deployDevice = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_deploy, task.pk),
    title: t`Deploy Device`,
    fields: taskDeployFields(),
    initialData: {
      latitude: deployment.latitude ?? task.site_detail?.latitude,
      longitude: deployment.longitude ?? task.site_detail?.longitude,
      depth_m: deployment.depth_m
    },
    preFormContent: (
      <Stack gap='xs'>
        <Alert color='blue'>
          {t`The device goes into the water: the deployment becomes deployed and its monitoring starts. The position is optional.`}
        </Alert>
        {isSwap && (
          <Alert color='orange' title={t`Swap`}>
            {t`The device it replaces is recovered into the "Removed" location of the field trip (or the fleet workshop without a trip). Reconciling the trip moves it to the workshop.`}
          </Alert>
        )}
      </Stack>
    ),
    successMessage: t`Device deployed`,
    onFormSuccess: onUpdate
  });

  const consume = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_consume, task.pk),
    title: t`Use Consumable`,
    fields: consumeFields(trip?.kit_location ?? undefined),
    initialData: { quantity: 1 },
    successMessage: t`Consumable used`,
    onFormSuccess: onUpdate
  });

  const reposition = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_reposition, task.pk),
    title: t`Reposition Device`,
    fields: repositionFields(),
    preFormContent: (
      <Alert color='blue'>
        {t`The new nominal position of the device. The geofence moves with it; the old position is kept in the action.`}
      </Alert>
    ),
    successMessage: t`Position updated`,
    onFormSuccess: onUpdate
  });

  const recordAction = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_record_action, task.pk),
    title: t`Record Action`,
    fields: recordActionFields(task.pk),
    initialData: {
      action: checklistAction.action ?? 'REPAIR',
      checklist_result: checklistAction.checklist_result,
      fault_code: checklistAction.fault_code
    },
    successMessage: t`Action recorded`,
    onFormSuccess: onUpdate
  });

  const complete = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_task_complete, task.pk),
    title: t`Complete Task`,
    fields: completeTaskFields(needsOverride),
    initialData: {
      labour_minutes: task.labour_minutes,
      summary: task.summary
    },
    preFormContent: needsOverride ? (
      <Alert color='orange' title={t`Data check not passed`}>
        {t`Run "Verify data" first, or give a reason to close the task anyway. A follow-up task is then created.`}
      </Alert>
    ) : undefined,
    successMessage: t`Task completed`,
    onFormSuccess: onUpdate
  });

  const openRecordAction = useCallback(
    (data: any) => {
      setChecklistAction(data);
      recordAction.open();
    },
    [recordAction]
  );

  const verify = useCallback(() => {
    setVerifying(true);

    api
      .post(apiUrl(ApiEndpoints.fleet_task_verify, task.pk), {})
      .then(() => onUpdate())
      .catch((error) => {
        showApiErrorMessage({
          error: error,
          title: t`Verify data`,
          message: t`The live data could not be checked`
        });
      })
      .finally(() => setVerifying(false));
  }, [api, task.pk, onUpdate]);

  return (
    <>
      {deployDevice.modal}
      {consume.modal}
      {reposition.modal}
      {recordAction.modal}
      {complete.modal}
      <Stack gap='sm'>
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing='sm'>
          <Paper withBorder p='sm'>
            <Stack gap='xs'>
              <Title order={5}>{t`Checklist`}</Title>
              <ChecklistEditor
                taskId={task.pk}
                editable={canChange}
                onChange={onUpdate}
                onAction={(row: any) =>
                  openRecordAction({
                    action: 'REPAIR',
                    checklist_result: row.pk,
                    fault_code: row.fault_code
                  })
                }
              />
            </Stack>
          </Paper>
          <Stack gap='sm'>
            {canDeploy && (
              <Paper withBorder p='sm'>
                <Stack gap='xs'>
                  <Title order={5}>{t`Deploy`}</Title>
                  <Text size='sm' c='dimmed'>
                    {isSwap
                      ? t`Put the new device in the water. The replaced device goes to the "Removed" location of the trip.`
                      : t`Put the device in the water.`}
                  </Text>
                  <Group gap='xs'>
                    <Button
                      color='blue'
                      leftSection={<IconAnchor size={18} />}
                      disabled={!canService}
                      onClick={() => deployDevice.open()}
                    >
                      {t`Deploy Device`}
                    </Button>
                  </Group>
                  {!canService && (
                    <Text size='xs' c='dimmed'>
                      {t`Deploying also needs the stock change permission.`}
                    </Text>
                  )}
                </Stack>
              </Paper>
            )}
            {deployed && (
              <Paper withBorder p='sm'>
                <Stack gap='xs'>
                  <Title order={5}>{t`Live Streams`}</Title>
                  <StreamStatusList deploymentId={task.deployment} />
                </Stack>
              </Paper>
            )}
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Actions`}</Title>
                <Group gap='xs'>
                  <Button
                    variant='outline'
                    leftSection={<IconDroplet size={18} />}
                    disabled={!canService}
                    onClick={() => consume.open()}
                  >
                    {t`Consume`}
                  </Button>
                  <Button
                    variant='outline'
                    leftSection={<IconTool size={18} />}
                    disabled={!canChange}
                    onClick={() => openRecordAction({ action: 'REPAIR' })}
                  >
                    {t`Repair / Note`}
                  </Button>
                  {deployed && (
                    <Button
                      variant='outline'
                      leftSection={<IconCurrentLocation size={18} />}
                      disabled={!canChange}
                      onClick={() => reposition.open()}
                    >
                      {t`Reposition`}
                    </Button>
                  )}
                </Group>
                <Group gap={4}>
                  <IconPhoto size={16} />
                  <Text size='xs' c='dimmed'>
                    {t`Photos: add them in the Attachments panel of this task.`}
                  </Text>
                </Group>
              </Stack>
            </Paper>
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Verify and Close`}</Title>
                {deployed ? (
                  <>
                    <Text size='sm' c='dimmed'>
                      {t`Check that every essential stream has sent data since the work started.`}
                    </Text>
                    {verification && <VerifyResult result={verification} />}
                  </>
                ) : (
                  <Text size='sm' c='dimmed'>
                    {t`The device is not in the water, so no data check is needed.`}
                  </Text>
                )}
                <Group gap='xs'>
                  {deployed && (
                    <Button
                      variant='outline'
                      leftSection={<IconRefresh size={18} />}
                      loading={verifying}
                      disabled={!canChange}
                      onClick={verify}
                    >
                      {t`Verify data`}
                    </Button>
                  )}
                  <Button
                    color='green'
                    leftSection={<IconCircleCheck size={18} />}
                    disabled={!canChange || task.checklist_pending > 0}
                    onClick={() => complete.open()}
                  >
                    {t`Complete`}
                  </Button>
                </Group>
                {task.checklist_pending > 0 && (
                  <Text size='xs' c='orange'>
                    {t`Required checklist items to answer`}:{' '}
                    {task.checklist_pending}
                  </Text>
                )}
              </Stack>
            </Paper>
          </Stack>
        </SimpleGrid>
        <Title order={5}>{t`Installed Components`}</Title>
        {!canService && (
          <Alert color='gray'>
            {t`Changing components also needs the stock change permission.`}
          </Alert>
        )}
        <TaskComponents
          task={task}
          canService={canService}
          onChange={onUpdate}
        />
        <Title order={5}>{t`Recorded Actions`}</Title>
        <MaintenanceActionTable
          key={`actions-${task.action_count}`}
          taskId={task.pk}
          tableName='fleet-task-execution-actions'
        />
      </Stack>
    </>
  );
}
