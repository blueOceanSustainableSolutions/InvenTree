import { t } from '@lingui/core/macro';
import { Alert, Skeleton, Stack, Text } from '@mantine/core';
import {
  IconActivityHeartbeat,
  IconAlertTriangle,
  IconFlag,
  IconInfoCircle,
  IconMap,
  IconTimeline,
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
import { formatDate } from '../../defaults/formatters';
import {
  DeviceStateBadge,
  FleetStatus,
  FleetStatusBadge,
  RiskBadges,
  deviceStateInfo,
  fleetStatusColor,
  userDisplayName
} from '../../fleet/components/FleetBadges';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { deploymentDetailFields } from '../../fleet/details/FleetDetailFields';
import {
  useDeploymentActions,
  useDeploymentState
} from '../../fleet/hooks/DeploymentActions';
import { DeploymentHealthPanel } from '../../fleet/panels/DeploymentHealthPanel';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';

/**
 * Detail page for a fleet deployment (pipeline, deployed or closed)
 */
export default function DeploymentDetail() {
  const { id } = useParams();
  const user = useUserState();
  const navigate = useNavigate();

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

  const { isPipeline, isDeployed } = useDeploymentState(deployment);
  const hasPosition =
    deployment.latitude != null && deployment.longitude != null;

  const detailsPanel = useMemo(() => {
    if (instanceQuery.isFetching) {
      return <Skeleton />;
    }

    const { left, right } = deploymentDetailFields(deployment, {
      isDeployed,
      hasPosition
    });

    return (
      <Stack gap='xs'>
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
            <Stack gap={2}>
              <Text size='sm'>
                {deviceStateInfo(deployment.internal_state).description}
              </Text>
              {deployment.internal_state_note && (
                <Text size='sm'>{deployment.internal_state_note}</Text>
              )}
              <Text size='xs' c='dimmed'>
                {[
                  userDisplayName(deployment.internal_state_changed_by_detail),
                  deployment.internal_state_changed_at
                    ? formatDate(deployment.internal_state_changed_at, {
                        showTime: true
                      })
                    : null
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Text>
            </Stack>
          </Alert>
        )}
        <ItemDetailsGrid>
          <DetailsTable title={t`Deployment`} fields={left} item={deployment} />
          <DetailsTable
            title={t`Dates and Position`}
            fields={right}
            item={deployment}
          />
        </ItemDetailsGrid>
      </Stack>
    );
  }, [deployment, instanceQuery, isDeployed, hasPosition]);

  const risksPanel = useMemo(() => {
    const risks: any[] = deployment.risks ?? [];

    if (risks.length == 0) {
      return (
        <Alert color='green' title={t`No readiness risks`}>
          {t`The device is on track for its deployment.`}
        </Alert>
      );
    }

    return (
      <Stack gap='xs'>
        {risks.map((risk: any) => (
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
    );
  }, [deployment]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'details',
        label: t`Deployment Details`,
        icon: <IconInfoCircle />,
        content: detailsPanel
      },
      {
        name: 'risks',
        label: t`Readiness`,
        icon: <IconTimeline />,
        hidden: !isPipeline,
        content: risksPanel
      },
      {
        name: 'health',
        label: t`Health and Streams`,
        icon: <IconActivityHeartbeat />,
        hidden: !isDeployed,
        content: deployment.pk ? (
          <DeploymentHealthPanel deployment={deployment} />
        ) : null
      },
      {
        name: 'map',
        label: t`Map and Track`,
        icon: <IconMap />,
        hidden: !isDeployed,
        content: deployment.pk ? (
          <FleetMapView deploymentId={deployment.pk} height={480} />
        ) : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        hidden: isPipeline,
        content: deployment.pk ? (
          <AlertTable
            params={{ deployment: deployment.pk }}
            tableName='fleet-deployment-alerts'
          />
        ) : null
      },
      {
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        hidden: isPipeline,
        content: deployment.pk ? (
          <TaskTable
            params={{ deployment: deployment.pk }}
            device={deployment.device}
            deployment={deployment.pk}
            tableName='fleet-deployment-tasks'
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
    [deployment, detailsPanel, risksPanel, isPipeline, isDeployed]
  );

  const deploymentActions = useDeploymentActions({
    deployment: deployment,
    onUpdate: refreshInstance,
    onDeleted: () => navigate('/fleet/index/pipeline')
  });

  const actions = useMemo(() => {
    const can = deploymentActions.state;

    return [
      <PrimaryActionButton
        key='create-build'
        title={t`Create Build Order`}
        icon='build'
        color='blue'
        hidden={!can.canCreateBuild}
        onClick={deploymentActions.openCreateBuild}
      />,
      <PrimaryActionButton
        key='assign-device'
        title={t`Assign Device`}
        icon='serial'
        color='cyan'
        hidden={!can.canAssignDevice}
        onClick={deploymentActions.openAssignDevice}
      />,
      <PrimaryActionButton
        key='deploy'
        title={t`Deploy`}
        icon='fleet_deployment'
        color='green'
        hidden={!can.canDeploy}
        onClick={deploymentActions.openDeploy}
      />,
      <PrimaryActionButton
        key='set-position'
        title={t`Set Position`}
        icon='location'
        color={hasPosition ? 'gray' : 'orange'}
        hidden={!can.canSetPosition}
        onClick={deploymentActions.openSetPosition}
      />,
      <PrimaryActionButton
        key='recover'
        title={t`Recover`}
        icon='return'
        color='orange'
        hidden={!can.canRecover}
        onClick={deploymentActions.openRecover}
      />,
      <AdminButton
        key='admin'
        model={ModelType.deployment}
        id={deployment.pk}
      />,
      <BarcodeActionDropdown
        key='barcode'
        model={ModelType.deployment}
        pk={deployment.pk}
        hash={deployment?.barcode_hash}
        perm={user.hasChangeRole(UserRoles.fleet)}
      />,
      <PrintingActions
        key='print'
        modelType={ModelType.deployment}
        items={[deployment.pk]}
        enableReports
      />,
      <OptionsActionDropdown
        key='options'
        tooltip={t`Deployment Actions`}
        actions={[
          EditItemAction({
            hidden: !can.canEdit,
            onClick: deploymentActions.openEdit
          }),
          {
            name: t`Set Device State`,
            tooltip: t`Problem acknowledged, docked or decommissioned`,
            icon: <IconFlag />,
            hidden: !can.canSetState,
            onClick: deploymentActions.openSetState
          },
          DeleteItemAction({
            hidden: !can.canDelete,
            onClick: deploymentActions.openDelete
          })
        ]}
      />
    ];
  }, [deployment, deploymentActions, hasPosition, user]);

  const badges = useMemo(() => {
    if (instanceQuery.isFetching) {
      return [];
    }

    return [
      <StatusRenderer
        key='status'
        status={deployment.status_custom_key || deployment.status}
        type={ModelType.deployment}
        options={{ size: 'lg' }}
      />,
      isDeployed ? (
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
    ].filter(Boolean);
  }, [deployment, instanceQuery, isDeployed]);

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
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <Stack gap='xs'>
          <PageDetail
            title={`${t`Deployment`}: ${deployment.reference ?? ''}`}
            subtitle={subtitle}
            badges={badges}
            breadcrumbs={[
              { name: t`Fleet`, url: '/fleet/' },
              {
                name: isPipeline ? t`Pipeline` : t`Deployments`,
                url: isPipeline
                  ? '/fleet/index/pipeline'
                  : '/fleet/index/deployments'
              }
            ]}
            lastCrumb={[
              {
                name: deployment.reference,
                url: getDetailUrl(ModelType.deployment, deployment.pk)
              }
            ]}
            actions={actions}
            editAction={deploymentActions.openEdit}
            editEnabled={deploymentActions.state.canEdit}
          />
          <PanelGroup
            pageKey='fleet-deployment'
            panels={panels}
            instance={deployment}
            reloadInstance={refreshInstance}
            model={ModelType.deployment}
            id={deployment.pk}
          />
        </Stack>
      </InstanceDetail>
    </>
  );
}
