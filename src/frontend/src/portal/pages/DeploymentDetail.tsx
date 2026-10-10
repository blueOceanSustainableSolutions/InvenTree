import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Menu,
  Modal,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconActivityHeartbeat,
  IconAlertTriangle,
  IconAnchor,
  IconArrowBackUp,
  IconCalendarEvent,
  IconComponents,
  IconCurrentLocation,
  IconDots,
  IconFlag,
  IconHammer,
  IconHistory,
  IconPackageImport,
  IconPencil,
  IconPlus,
  IconTimeline,
  IconTool,
  IconTrash
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { PanelType } from '@lib/types/Panel';
import InstanceDetail from '../../components/nav/InstanceDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { useApi } from '../../contexts/ApiContext';
import { ComponentTree } from '../../fleet/components/ComponentTree';
import { DeploymentTimeline } from '../../fleet/components/DeploymentTimeline';
import {
  DeviceStateBadge,
  FleetStatus,
  FleetStatusBadge,
  RiskBadges,
  deviceStateInfo,
  fleetStatusColor
} from '../../fleet/components/FleetBadges';
import { FleetMapView } from '../../fleet/components/FleetMap';
import {
  StreamStatusList,
  formatAge
} from '../../fleet/components/StreamStatusList';
import {
  deploymentDetailFields,
  detailFieldsByName
} from '../../fleet/details/FleetDetailFields';
import { useDeploymentActions } from '../../fleet/hooks/DeploymentActions';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { useTaskFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';
import { CardList, TaskCard } from '../components/PortalCards';
import { PortalDetailFields } from '../components/PortalDetails';
import { PortalPage } from '../components/PortalPage';
import { PortalTabs } from '../components/PortalTabs';
import { mainUiUrl } from '../navigation';
import { useIsPhone } from '../useIsPhone';

/**
 * "Start maintenance": the open tasks of the deployment (tap one to open it
 * and start it), or a new task for managers.
 */
function StartMaintenanceModal({
  deployment,
  opened,
  onClose
}: Readonly<{
  deployment: any;
  opened: boolean;
  onClose: () => void;
}>): ReactNode {
  const api = useApi();
  const user = useUserState();

  const query = useQuery({
    queryKey: ['fleet-portal-deployment-open-tasks', deployment.pk],
    enabled: opened && !!deployment.pk,
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_list), {
          params: {
            deployment: deployment.pk,
            open: true,
            ordering: 'due_date'
          }
        })
        .then((response) => response.data ?? [])
  });

  const createFields = useTaskFields({
    create: true,
    deviceId: deployment.device,
    deploymentId: deployment.pk
  });

  const newTask = useCreateApiFormModal({
    url: ApiEndpoints.fleet_task_list,
    title: t`New Maintenance Task`,
    fields: createFields,
    initialData: {
      task_type: 'CORRECTIVE',
      device: deployment.device,
      deployment: deployment.pk
    },
    follow: true,
    modelType: ModelType.maintenancetask
  });

  const tasks: any[] = query.data ?? [];

  return (
    <>
      {newTask.modal}
      <Modal
        opened={opened}
        onClose={onClose}
        title={t`Start Maintenance`}
        size='lg'
      >
        <Stack gap='sm'>
          {query.isLoading ? (
            <Skeleton h={80} />
          ) : (
            <CardList
              empty={t`There is no open task for this device.`}
              title={tasks.length > 0 ? t`Open Tasks` : undefined}
            >
              {tasks.map((task: any) => (
                <TaskCard key={task.pk} task={task} />
              ))}
            </CardList>
          )}
          {user.hasAddRole(UserRoles.fleet) ? (
            <Button
              variant='outline'
              leftSection={<IconPlus size={18} />}
              onClick={() => {
                onClose();
                newTask.open();
              }}
            >
              {t`New Task`}
            </Button>
          ) : (
            tasks.length == 0 && (
              <Text size='sm' c='dimmed'>
                {t`Ask a manager to plan a task for this device.`}
              </Text>
            )
          )}
        </Stack>
      </Modal>
    </>
  );
}

/**
 * Device card of a deployment (manager and technician): site, serial, live
 * streams, a map snippet, the installed components (read only), alerts, the
 * history timeline and maintenance, with the actions Start Maintenance,
 * Deploy, Recover and Set Position (and, in the pipeline, Create Build
 * Order, Assign Device, Set Site and Date). Site and position are optional.
 */
export default function DeploymentDetail(): ReactNode {
  const { id } = useParams();
  const navigate = useNavigate();
  const isPhone = useIsPhone();

  const [maintenanceOpened, setMaintenanceOpened] = useState<boolean>(false);

  const {
    instance: deployment,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_deployment_list,
    pk: id,
    hasPrimaryKey: true,
    params: {
      risks: true,
      client_detail: true
    }
  });

  const deploymentActions = useDeploymentActions({
    deployment: deployment,
    onUpdate: refreshInstance,
    onDeleted: () => navigate('/pipeline')
  });

  const state = deploymentActions.state;

  const deviceCard = useMemo(() => {
    if (!deployment.pk) {
      return <Skeleton h={160} />;
    }

    const site = deployment.site_detail;
    const device = deployment.device_detail;
    const fields = detailFieldsByName(
      deploymentDetailFields(deployment, {
        isDeployed: state.isDeployed,
        hasPosition: state.hasPosition
      })
    );

    return (
      <Paper withBorder p='sm'>
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 4 }} spacing='sm'>
          <PortalDetailFields
            item={deployment}
            fields={[
              {
                ...fields.site,
                hidden: false,
                value_formatter: () =>
                  site ? (
                    <Anchor
                      size='sm'
                      onClick={() => navigate(`/sites/${site.pk}`)}
                    >
                      {site.name}
                    </Anchor>
                  ) : (
                    <Text size='sm' c='dimmed'>{t`No site`}</Text>
                  )
              },
              {
                ...fields.device,
                hidden: false,
                value_formatter: () =>
                  device ? (
                    <Anchor
                      size='sm'
                      href={mainUiUrl(
                        getDetailUrl(ModelType.stockitem, deployment.device)
                      )}
                    >
                      {device.part_name} #{device.serial}
                    </Anchor>
                  ) : (
                    deployment.device_type_detail?.part_name
                  )
              },
              fields.platform_id,
              {
                ...fields.client,
                hidden: false,
                value_formatter: () => deployment.client_detail?.name
              },
              fields.coverage,
              {
                type: 'string',
                name: 'device_state',
                label: t`Device State`,
                value_formatter: () =>
                  deployment.device_state ? (
                    <DeviceStateBadge
                      state={deployment.device_state}
                      note={deployment.internal_state_note}
                    />
                  ) : undefined
              },
              {
                type: 'string',
                name: 'last_data',
                label: t`Last Data`,
                hidden: !state.isDeployed,
                value_formatter: () => formatAge(deployment.last_contact)
              },
              {
                ...fields.target_date,
                hidden: !state.isPipeline || !deployment.target_date
              },
              {
                type: 'date',
                name: 'deployed_at',
                label: fields.deployed_at.label
              },
              fields.next_pm_date,
              {
                type: 'string',
                name: 'position',
                label: t`Position`,
                value_formatter: () =>
                  state.hasPosition ? (
                    `${deployment.latitude}, ${deployment.longitude}${
                      deployment.depth_m != null
                        ? ` (${deployment.depth_m} m)`
                        : ''
                    }`
                  ) : state.isClosed ? undefined : (
                    <Text size='sm' c='orange'>{t`Not set`}</Text>
                  )
              },
              {
                ...fields.build,
                hidden: false,
                value_formatter: () =>
                  deployment.build ? (
                    <Anchor
                      size='sm'
                      href={mainUiUrl(
                        getDetailUrl(ModelType.build, deployment.build)
                      )}
                    >
                      {deployment.build_detail?.reference ?? deployment.build}
                    </Anchor>
                  ) : undefined
              }
            ]}
          />
        </SimpleGrid>
      </Paper>
    );
  }, [deployment, state, navigate]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'live',
        label: t`Live Data`,
        icon: <IconActivityHeartbeat />,
        hidden: !state.isDeployed,
        content: deployment.pk ? (
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing='sm'>
            <Paper withBorder p='sm'>
              <Stack gap='xs'>
                <Title order={5}>{t`Data Streams`}</Title>
                <StreamStatusList deploymentId={deployment.pk} />
              </Stack>
            </Paper>
            <Paper withBorder p={4}>
              <FleetMapView
                deploymentId={deployment.pk}
                height={isPhone ? 240 : 320}
              />
            </Paper>
          </SimpleGrid>
        ) : null
      },
      {
        name: 'readiness',
        label: t`Readiness`,
        icon: <IconTimeline />,
        hidden: !state.isPipeline,
        content:
          (deployment.risks ?? []).length == 0 ? (
            <Alert color='green' title={t`No readiness risks`}>
              {t`The device is on track for its deployment.`}
            </Alert>
          ) : (
            <Stack gap='xs'>
              {(deployment.risks ?? []).map((risk: any) => (
                <Alert
                  key={risk.code}
                  color={fleetStatusColor(FleetStatus.severity, risk.severity)}
                  icon={<IconAlertTriangle />}
                  title={risk.code}
                >
                  {risk.message}
                </Alert>
              ))}
            </Stack>
          )
      },
      {
        name: 'components',
        label: t`Components`,
        icon: <IconComponents />,
        hidden: !deployment.device,
        content: deployment.device ? (
          <Paper withBorder p='sm'>
            <Stack gap='xs'>
              <Text size='sm' c='dimmed'>
                {t`Installed in the device. Components are changed in a maintenance task.`}
              </Text>
              <ComponentTree deviceId={deployment.device} />
            </Stack>
          </Paper>
        ) : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        hidden: state.isPipeline,
        content: deployment.pk ? (
          <AlertTable
            params={{ deployment: deployment.pk }}
            tableName='fleet-portal-deployment-alerts'
          />
        ) : null
      },
      {
        name: 'history',
        label: t`History`,
        icon: <IconHistory />,
        content: deployment.pk ? (
          <DeploymentTimeline deployment={deployment} />
        ) : null
      },
      {
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        hidden: state.isPipeline,
        content: deployment.pk ? (
          <TaskTable
            params={{ deployment: deployment.pk }}
            device={deployment.device}
            deployment={deployment.pk}
            tableName='fleet-portal-deployment-tasks'
          />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.deployment,
        model_id: deployment.pk
      }),
      NotesPanel({
        model_type: ModelType.deployment,
        model_id: deployment.pk,
        has_note: !!deployment.notes
      })
    ],
    [deployment, state, isPhone]
  );

  const canMaintain =
    !!deployment.device &&
    !state.isClosed &&
    state.canEdit &&
    !state.isPipeline;

  const actions = [
    canMaintain ? (
      <Button
        key='maintenance'
        color='blue'
        leftSection={<IconTool size={18} />}
        onClick={() => setMaintenanceOpened(true)}
      >
        {t`Start Maintenance`}
      </Button>
    ) : null,
    state.canDeploy ? (
      <Button
        key='deploy'
        color='green'
        leftSection={<IconAnchor size={18} />}
        onClick={deploymentActions.openDeploy}
      >
        {t`Deploy`}
      </Button>
    ) : null,
    state.canRecover ? (
      <Button
        key='recover'
        color='orange'
        variant='outline'
        leftSection={<IconArrowBackUp size={18} />}
        onClick={deploymentActions.openRecover}
      >
        {t`Recover`}
      </Button>
    ) : null,
    state.canSetPosition ? (
      <Button
        key='position'
        variant='outline'
        color={state.hasPosition ? 'gray' : 'orange'}
        leftSection={<IconCurrentLocation size={18} />}
        onClick={deploymentActions.openSetPosition}
      >
        {t`Set Position`}
      </Button>
    ) : null,
    state.canSetState ? (
      <Button
        key='set-state'
        variant='outline'
        color={state.internalState ? 'grape' : 'gray'}
        leftSection={<IconFlag size={18} />}
        onClick={deploymentActions.openSetState}
      >
        {t`Set State`}
      </Button>
    ) : null,
    state.canCreateBuild ? (
      <Button
        key='create-build'
        variant='outline'
        leftSection={<IconHammer size={18} />}
        onClick={deploymentActions.openCreateBuild}
      >
        {t`Create Build Order`}
      </Button>
    ) : null,
    state.canAssignDevice ? (
      <Button
        key='assign-device'
        variant='outline'
        color='cyan'
        leftSection={<IconPackageImport size={18} />}
        onClick={deploymentActions.openAssignDevice}
      >
        {t`Assign Device`}
      </Button>
    ) : null,
    state.canEdit || state.canDelete ? (
      <Menu key='options' position='bottom-end'>
        <Menu.Target>
          <Button
            variant='subtle'
            leftSection={<IconDots size={18} />}
            aria-label='deployment-options'
          >
            {t`More`}
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          {state.canEdit && state.isPipeline && (
            <Menu.Item
              leftSection={<IconCalendarEvent size={18} />}
              onClick={deploymentActions.openPlan}
            >
              {t`Set Site and Date`}
            </Menu.Item>
          )}
          {state.canEdit && (
            <Menu.Item
              leftSection={<IconPencil size={18} />}
              onClick={deploymentActions.openEdit}
            >
              {t`Edit`}
            </Menu.Item>
          )}
          {state.canDelete && (
            <Menu.Item
              color='red'
              leftSection={<IconTrash size={18} />}
              onClick={deploymentActions.openDelete}
            >
              {t`Delete`}
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    ) : null
  ];

  const subtitle = [
    deployment.site_detail?.name,
    deployment.device_detail
      ? `#${deployment.device_detail.serial}`
      : deployment.device_type_detail?.part_name
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {deploymentActions.modals}
      {deployment.pk && (
        <StartMaintenanceModal
          deployment={deployment}
          opened={maintenanceOpened}
          onClose={() => setMaintenanceOpened(false)}
        />
      )}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <PortalPage
          title={`${t`Deployment`} ${deployment.reference ?? ''}`}
          subtitle={subtitle}
          back={state.isPipeline ? '/pipeline' : '/'}
          badges={[
            <StatusRenderer
              key='status'
              status={deployment.status_custom_key || deployment.status}
              type={ModelType.deployment}
              options={{ size: 'lg' }}
            />,
            state.isDeployed ? (
              <FleetStatusBadge
                key='health'
                type={FleetStatus.health}
                status={deployment.health}
                size='lg'
              />
            ) : null,
            <DeviceStateBadge
              key='device-state'
              state={deployment.device_state}
              note={deployment.internal_state_note}
              size='lg'
            />,
            <RiskBadges key='risks' risks={deployment.risks} />
          ]}
          actions={actions}
        >
          {deployment.metadata_warning && (
            <Alert color='orange' icon={<IconAlertTriangle />}>
              {deployment.metadata_warning}
            </Alert>
          )}
          {deployment.internal_state && (
            <Alert
              color={deviceStateInfo(deployment.internal_state).color}
              icon={<IconFlag />}
              title={deviceStateInfo(deployment.internal_state).label}
            >
              {deployment.internal_state_note ||
                deviceStateInfo(deployment.internal_state).description}
            </Alert>
          )}
          {deviceCard}
          <PortalTabs panels={panels} />
        </PortalPage>
      </InstanceDetail>
    </>
  );
}
