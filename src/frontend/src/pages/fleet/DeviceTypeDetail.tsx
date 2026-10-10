import { t } from '@lingui/core/macro';
import { Skeleton, Stack } from '@mantine/core';
import {
  IconAnchor,
  IconChecklist,
  IconInfoCircle,
  IconPackages,
  IconTimelineEvent
} from '@tabler/icons-react';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { PanelType } from '@lib/types/Panel';
import AdminButton from '../../components/buttons/AdminButton';
import {
  type DetailsField,
  DetailsTable
} from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import {
  DeleteItemAction,
  EditItemAction,
  OptionsActionDropdown
} from '../../components/items/ActionDropdown';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import NotesPanel from '../../components/panels/NotesPanel';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { ChecklistTemplateTable } from '../../fleet/tables/ChecklistTemplateTable';
import { DeploymentTable } from '../../fleet/tables/DeploymentTable';
import { KitTemplateTable } from '../../fleet/tables/KitTemplateTable';
import { StreamTemplateTable } from '../../fleet/tables/StreamTemplateTable';
import { useDeviceTypeFields } from '../../forms/FleetForms';
import {
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';

/**
 * Detail page for a fleet device type: maintenance rules, data streams,
 * checklist and kit templates
 */
export default function DeviceTypeDetail() {
  const { id } = useParams();
  const user = useUserState();
  const navigate = useNavigate();

  const {
    instance: deviceType,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_device_type_list,
    pk: id,
    hasPrimaryKey: true
  });

  const detailsPanel = useMemo(() => {
    if (instanceQuery.isFetching) {
      return <Skeleton />;
    }

    const fields: DetailsField[] = [
      {
        type: 'link',
        name: 'part',
        label: t`Part`,
        icon: 'part',
        model: ModelType.part
      },
      {
        type: 'number',
        name: 'pm_interval_days',
        label: t`PM Interval (days)`,
        icon: 'calendar'
      },
      {
        type: 'number',
        name: 'verify_window_minutes',
        label: t`Verify Window (minutes)`,
        icon: 'info'
      },
      {
        type: 'number',
        name: 'deployment_count',
        label: t`Deployed`,
        icon: 'fleet_deployment'
      },
      {
        type: 'boolean',
        name: 'active',
        label: t`Active`,
        icon: 'active'
      }
    ];

    return (
      <ItemDetailsGrid>
        <DetailsTable
          title={t`Device Type`}
          fields={fields}
          item={deviceType}
        />
      </ItemDetailsGrid>
    );
  }, [deviceType, instanceQuery]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'details',
        label: t`Device Type Details`,
        icon: <IconInfoCircle />,
        content: detailsPanel
      },
      {
        name: 'streams',
        label: t`Data Streams`,
        icon: <IconTimelineEvent />,
        content: deviceType.pk ? (
          <StreamTemplateTable deviceTypeId={deviceType.pk} />
        ) : (
          <Skeleton />
        )
      },
      {
        name: 'checklist',
        label: t`Checklist`,
        icon: <IconChecklist />,
        content: deviceType.pk ? (
          <ChecklistTemplateTable deviceTypeId={deviceType.pk} />
        ) : (
          <Skeleton />
        )
      },
      {
        name: 'kit',
        label: t`Parts Kit`,
        icon: <IconPackages />,
        content: deviceType.pk ? (
          <KitTemplateTable deviceTypeId={deviceType.pk} />
        ) : (
          <Skeleton />
        )
      },
      {
        name: 'deployments',
        label: t`Deployments`,
        icon: <IconAnchor />,
        content: deviceType.pk ? (
          <DeploymentTable
            tableName='fleet-device-type-deployments'
            params={{ device_type: deviceType.pk }}
          />
        ) : (
          <Skeleton />
        )
      },
      NotesPanel({
        model_type: ModelType.fleetdevicetype,
        model_id: deviceType.pk,
        has_note: !!deviceType.notes
      })
    ],
    [deviceType, detailsPanel]
  );

  const fields = useDeviceTypeFields({ partId: deviceType.part });

  const editDeviceType = useEditApiFormModal({
    url: ApiEndpoints.fleet_device_type_list,
    pk: deviceType.pk,
    title: t`Edit Device Type`,
    fields: fields,
    onFormSuccess: refreshInstance
  });

  const deleteDeviceType = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_device_type_list,
    pk: deviceType.pk,
    title: t`Delete Device Type`,
    onFormSuccess: () => navigate('/fleet/index/device-types')
  });

  const actions = useMemo(
    () => [
      <AdminButton
        key='admin'
        model={ModelType.fleetdevicetype}
        id={deviceType.pk}
      />,
      <OptionsActionDropdown
        key='options'
        tooltip={t`Device Type Actions`}
        actions={[
          EditItemAction({
            hidden: !user.hasChangeRole(UserRoles.fleet),
            onClick: () => editDeviceType.open()
          }),
          DeleteItemAction({
            hidden: !user.hasDeleteRole(UserRoles.fleet),
            onClick: () => deleteDeviceType.open()
          })
        ]}
      />
    ],
    [user, deviceType]
  );

  const name = deviceType.part_detail?.full_name ?? '';

  return (
    <>
      {editDeviceType.modal}
      {deleteDeviceType.modal}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <Stack gap='xs'>
          <PageDetail
            title={`${t`Device Type`}: ${name}`}
            imageUrl={deviceType.part_detail?.thumbnail}
            breadcrumbs={[
              { name: t`Fleet`, url: '/fleet/' },
              { name: t`Device Types`, url: '/fleet/index/device-types' }
            ]}
            lastCrumb={[
              {
                name: name,
                url: getDetailUrl(ModelType.fleetdevicetype, deviceType.pk)
              }
            ]}
            actions={actions}
            editAction={editDeviceType.open}
            editEnabled={user.hasChangeRole(UserRoles.fleet)}
          />
          <PanelGroup
            pageKey='fleet-device-type'
            panels={panels}
            instance={deviceType}
            reloadInstance={refreshInstance}
            model={ModelType.fleetdevicetype}
            id={deviceType.pk}
          />
        </Stack>
      </InstanceDetail>
    </>
  );
}
