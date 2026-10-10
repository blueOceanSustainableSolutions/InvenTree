import { t } from '@lingui/core/macro';
import { Alert, List } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { type ReactNode, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import {
  assignDeviceFields,
  deployFields,
  planDeploymentFields,
  recoverFields,
  setDeviceStateFields,
  setPositionFields,
  useDeploymentFields
} from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';

/** What a deployment is, and which actions the user may run on it */
export type DeploymentState = {
  isPipeline: boolean;
  isDeployed: boolean;
  isClosed: boolean;
  hasPosition: boolean;
  canEdit: boolean;
  canDelete: boolean;
  canCreateBuild: boolean;
  canAssignDevice: boolean;
  canDeploy: boolean;
  canSetPosition: boolean;
  canRecover: boolean;
  /** Effective internal state (ACTIVE, DOCKED, ...), null outside of deployed */
  deviceState: string | null;
  /** Manual state stored on the device ('' when none) */
  internalState: string;
  isDocked: boolean;
  isDecommissioned: boolean;
  canSetState: boolean;
};

/**
 * The state of a deployment and the actions allowed to the user
 */
export function useDeploymentState(deployment: any): DeploymentState {
  const user = useUserState();
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });
  const buildStatus = useStatusCodes({ modelType: ModelType.build });

  return useMemo(() => {
    const status = deployment?.status;
    const isPipeline = [
      deploymentStatus.PLANNED,
      deploymentStatus.IN_PRODUCTION,
      deploymentStatus.READY,
      deploymentStatus.SCHEDULED
    ].includes(status);
    const isDeployed = status == deploymentStatus.DEPLOYED;
    const isClosed =
      status == deploymentStatus.RECOVERED ||
      status == deploymentStatus.CANCELLED;
    const hasPosition =
      deployment?.latitude != null && deployment?.longitude != null;

    const canChange = user.hasChangeRole(UserRoles.fleet);
    const canMoveStock = canChange && user.hasChangeRole(UserRoles.stock);
    // Build order still open: pending, in production or on hold
    const buildOpen =
      !!deployment?.build_detail &&
      [
        buildStatus.PENDING,
        buildStatus.PRODUCTION,
        buildStatus.ON_HOLD
      ].includes(deployment.build_detail.status);

    return {
      isPipeline,
      isDeployed,
      isClosed,
      hasPosition,
      canEdit: !!deployment?.pk && canChange,
      canDelete:
        !!deployment?.pk &&
        user.hasDeleteRole(UserRoles.fleet) &&
        (status == deploymentStatus.PLANNED ||
          status == deploymentStatus.CANCELLED),
      canCreateBuild:
        status == deploymentStatus.PLANNED &&
        !deployment?.build &&
        !deployment?.device &&
        user.hasAddRole(UserRoles.fleet) &&
        user.hasAddRole(UserRoles.build),
      canAssignDevice:
        isPipeline && !buildOpen && !deployment?.device && canChange,
      canDeploy:
        (status == deploymentStatus.READY ||
          status == deploymentStatus.SCHEDULED) &&
        canMoveStock,
      canSetPosition: !isClosed && !!deployment?.pk && canChange,
      canRecover: isDeployed && canMoveStock,
      deviceState: deployment?.device_state ?? null,
      internalState: deployment?.internal_state ?? '',
      isDocked: deployment?.internal_state == 'DOCKED',
      isDecommissioned: deployment?.internal_state == 'DECOMMISSIONED',
      // Technicians and managers; not for a device still in the pipeline
      canSetState:
        !!deployment?.pk && !!deployment?.device && !isPipeline && canChange
    };
  }, [deployment, user, deploymentStatus, buildStatus]);
}

/**
 * The action forms of a deployment, shared by the deployment page of the main
 * UI, the Fleet Portal and the pipeline table: edit, plan (site and date),
 * delete, create build order, assign device, deploy, set position, recover,
 * set device state.
 *
 * Render `modals` once; call the `open*` functions from buttons.
 */
export function useDeploymentActions({
  deployment,
  onUpdate,
  onDeleted
}: {
  deployment: any;
  onUpdate: () => void;
  onDeleted?: () => void;
}) {
  const state = useDeploymentState(deployment);

  const editFields = useDeploymentFields({ create: false });

  const editDeployment = useEditApiFormModal({
    url: ApiEndpoints.fleet_deployment_list,
    pk: deployment?.pk,
    title: t`Edit Deployment`,
    fields: editFields,
    onFormSuccess: onUpdate
  });

  const planDeployment = useEditApiFormModal({
    url: ApiEndpoints.fleet_deployment_list,
    pk: deployment?.pk,
    title: t`Set Site and Date`,
    fields: planDeploymentFields(),
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {t`Where and when the device is deployed. Both can be left empty and set later.`}
      </Alert>
    ),
    onFormSuccess: onUpdate
  });

  const deleteDeployment = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_deployment_list,
    pk: deployment?.pk,
    title: t`Delete Deployment`,
    onFormSuccess: () => onDeleted?.()
  });

  const createBuild = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_create_build, deployment?.pk),
    title: t`Create Build Order`,
    preFormWarning: t`Create a build order (quantity 1) for this deployment. Issue the build order to start production.`,
    successMessage: t`Build order created`,
    onFormSuccess: onUpdate
  });

  const assignDevice = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_assign_device, deployment?.pk),
    title: t`Assign Existing Device`,
    fields: assignDeviceFields(deployment ?? {}),
    successMessage: t`Device assigned`,
    onFormSuccess: onUpdate
  });

  const deploy = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_deploy, deployment?.pk),
    title: t`Deploy Device`,
    fields: deployFields(),
    initialData: {
      latitude: deployment?.latitude ?? deployment?.site_detail?.latitude,
      longitude: deployment?.longitude ?? deployment?.site_detail?.longitude
    },
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {t`The device is assigned to the client of the deployment or site (or the default fleet customer) and leaves stock. The site and the position are optional and can be set later.`}
      </Alert>
    ),
    successMessage: t`Device deployed`,
    onFormSuccess: onUpdate
  });

  const setPosition = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_set_position, deployment?.pk),
    title: t`Set Position`,
    fields: setPositionFields(),
    initialData: {
      latitude: deployment?.latitude ?? deployment?.site_detail?.latitude,
      longitude: deployment?.longitude ?? deployment?.site_detail?.longitude,
      depth_m: deployment?.depth_m,
      geofence_radius_m: deployment?.geofence_radius_m
    },
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {t`The nominal position of the device. The geofence is centred on it.`}
      </Alert>
    ),
    successMessage: t`Position updated`,
    onFormSuccess: onUpdate
  });

  const setState = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_set_state, deployment?.pk),
    title: t`Set Device State`,
    fields: setDeviceStateFields(state.isDeployed),
    initialData: {
      state: deployment?.internal_state ?? '',
      note: ''
    },
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        <List size='sm'>
          <List.Item>
            {t`Problem Acknowledged: the problem is known. Monitoring goes on, but no notifications are sent until the device is healthy again or a task on it is completed.`}
          </List.Item>
          <List.Item>
            {t`Docked: back on land, still with the customer. Not monitored, no preventive maintenance, not on the map or in the overview.`}
          </List.Item>
          <List.Item>
            {t`Decommissioned: will not become active again. The deployment is closed (the stock item keeps its customer), open tasks are cancelled, and the stock sync never reopens it.`}
          </List.Item>
        </List>
      </Alert>
    ),
    successMessage: t`Device state updated`,
    onFormSuccess: onUpdate
  });

  const recover = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_deployment_recover, deployment?.pk),
    title: t`Recover Device`,
    fields: recoverFields(),
    preFormWarning: t`The device is returned from the customer into the selected location (default: the fleet workshop).`,
    successMessage: t`Device recovered`,
    onFormSuccess: onUpdate
  });

  const modals: ReactNode = (
    <>
      {editDeployment.modal}
      {planDeployment.modal}
      {deleteDeployment.modal}
      {createBuild.modal}
      {assignDevice.modal}
      {deploy.modal}
      {setPosition.modal}
      {recover.modal}
      {setState.modal}
    </>
  );

  return {
    state,
    modals,
    openEdit: editDeployment.open,
    openPlan: planDeployment.open,
    openDelete: deleteDeployment.open,
    openCreateBuild: createBuild.open,
    openAssignDevice: assignDevice.open,
    openDeploy: deploy.open,
    openSetPosition: setPosition.open,
    openRecover: recover.open,
    openSetState: setState.open
  };
}
