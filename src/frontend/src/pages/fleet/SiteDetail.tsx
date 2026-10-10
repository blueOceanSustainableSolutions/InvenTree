import { t } from '@lingui/core/macro';
import { Skeleton, Stack } from '@mantine/core';
import {
  IconAlertTriangle,
  IconAnchor,
  IconInfoCircle,
  IconMap,
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
import { DetailsTable } from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import {
  DeleteItemAction,
  EditItemAction,
  OptionsActionDropdown
} from '../../components/items/ActionDropdown';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import AttachmentPanel from '../../components/panels/AttachmentPanel';
import NotesPanel from '../../components/panels/NotesPanel';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { CoverageBadge } from '../../fleet/components/FleetBadges';
import { FleetMapView } from '../../fleet/components/FleetMap';
import { siteDetailFields } from '../../fleet/details/FleetDetailFields';
import { AlertTable } from '../../fleet/tables/AlertTable';
import { DeploymentTable } from '../../fleet/tables/DeploymentTable';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { useSiteFields } from '../../forms/FleetForms';
import {
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';

/**
 * Detail page for a fleet site
 */
export default function SiteDetail() {
  const { id } = useParams();
  const user = useUserState();
  const navigate = useNavigate();

  const {
    instance: site,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_site_list,
    pk: id,
    hasPrimaryKey: true
  });

  const detailsPanel = useMemo(() => {
    if (instanceQuery.isFetching) {
      return <Skeleton />;
    }

    const { left, right } = siteDetailFields(site);

    return (
      <ItemDetailsGrid>
        <DetailsTable title={t`Site`} fields={left} item={site} />
        <DetailsTable title={t`Position`} fields={right} item={site} />
      </ItemDetailsGrid>
    );
  }, [site, instanceQuery]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'details',
        label: t`Site Details`,
        icon: <IconInfoCircle />,
        content: detailsPanel
      },
      {
        name: 'deployments',
        label: t`Deployments`,
        icon: <IconAnchor />,
        content: site.pk ? (
          <DeploymentTable
            tableName='fleet-site-deployments'
            params={{ site: site.pk }}
          />
        ) : (
          <Skeleton />
        )
      },
      {
        name: 'map',
        label: t`Map`,
        icon: <IconMap />,
        hidden: !site.active_deployment,
        content: site.pk ? (
          <FleetMapView params={{ site: site.pk }} height={420} />
        ) : null
      },
      {
        name: 'alerts',
        label: t`Alerts`,
        icon: <IconAlertTriangle />,
        content: site.pk ? (
          <AlertTable
            params={{ site: site.pk }}
            tableName='fleet-site-alerts'
          />
        ) : null
      },
      {
        name: 'maintenance',
        label: t`Maintenance`,
        icon: <IconTool />,
        content: site.pk ? (
          <TaskTable params={{ site: site.pk }} tableName='fleet-site-tasks' />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.site,
        model_id: site.pk
      }),
      NotesPanel({
        model_type: ModelType.site,
        model_id: site.pk,
        has_note: !!site.notes
      })
    ],
    [site, detailsPanel]
  );

  const siteFields = useSiteFields();

  const editSite = useEditApiFormModal({
    url: ApiEndpoints.fleet_site_list,
    pk: site.pk,
    title: t`Edit Site`,
    fields: siteFields,
    onFormSuccess: refreshInstance
  });

  const deleteSite = useDeleteApiFormModal({
    url: ApiEndpoints.fleet_site_list,
    pk: site.pk,
    title: t`Delete Site`,
    onFormSuccess: () => navigate('/fleet/index/sites')
  });

  const actions = useMemo(
    () => [
      <AdminButton key='admin' model={ModelType.site} id={site.pk} />,
      <OptionsActionDropdown
        key='options'
        tooltip={t`Site Actions`}
        actions={[
          EditItemAction({
            hidden: !user.hasChangeRole(UserRoles.fleet),
            onClick: () => editSite.open()
          }),
          DeleteItemAction({
            hidden: !user.hasDeleteRole(UserRoles.fleet),
            onClick: () => deleteSite.open()
          })
        ]}
      />
    ],
    [user, site]
  );

  const badges = useMemo(
    () =>
      instanceQuery.isFetching
        ? []
        : [<CoverageBadge key='coverage' coverage={site.coverage} size='lg' />],
    [site, instanceQuery]
  );

  return (
    <>
      {editSite.modal}
      {deleteSite.modal}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <Stack gap='xs'>
          <PageDetail
            title={`${t`Site`}: ${site.name ?? ''}`}
            subtitle={site.reference}
            badges={badges}
            breadcrumbs={[
              { name: t`Fleet`, url: '/fleet/' },
              { name: t`Sites`, url: '/fleet/index/sites' }
            ]}
            lastCrumb={[
              {
                name: site.reference,
                url: getDetailUrl(ModelType.site, site.pk)
              }
            ]}
            actions={actions}
            editAction={editSite.open}
            editEnabled={user.hasChangeRole(UserRoles.fleet)}
          />
          <PanelGroup
            pageKey='fleet-site'
            panels={panels}
            instance={site}
            reloadInstance={refreshInstance}
            model={ModelType.site}
            id={site.pk}
          />
        </Stack>
      </InstanceDetail>
    </>
  );
}
