import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Paper,
  Skeleton,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconArrowBackUp,
  IconInfoCircle,
  IconListCheck,
  IconPackages,
  IconPlus
} from '@tabler/icons-react';
import { useCallback, useMemo } from 'react';
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
import { TripStatusSteps } from '../../fleet/components/TripStatusSteps';
import { tripDetailFields } from '../../fleet/details/FleetDetailFields';
import { useTripActions } from '../../fleet/hooks/TripActions';
import { TripReconcilePanel } from '../../fleet/panels/TripReconcilePanel';
import { TaskTable } from '../../fleet/tables/TaskTable';
import { KitStockTable, TripKitTable } from '../../fleet/tables/TripKitTable';
import { useInstance } from '../../hooks/UseInstance';
import { useUserState } from '../../states/UserState';

/**
 * Detail page for a fleet field trip: details and status steps, tasks, the
 * kit (suggest, prepare, contents), reconcile, attachments and notes. The
 * trip report is printed from the print menu.
 */
export default function TripDetail() {
  const { id } = useParams();
  const user = useUserState();
  const navigate = useNavigate();

  const {
    instance: trip,
    instanceQuery,
    refreshInstance
  } = useInstance({
    endpoint: ApiEndpoints.fleet_trip_list,
    pk: id,
    hasPrimaryKey: true
  });

  const { instance: kit, refreshInstance: refreshKit } = useInstance({
    endpoint: ApiEndpoints.fleet_trip_kit,
    pk: id,
    hasPrimaryKey: true,
    defaultValue: {}
  });

  const refreshAll = useCallback(() => {
    refreshInstance();
    refreshKit();
  }, [refreshInstance, refreshKit]);

  const tripActions = useTripActions({
    trip: trip,
    kit: kit,
    onUpdate: refreshAll,
    onDeleted: () => navigate('/fleet/index/trips')
  });

  // Tasks can be added and the kit changed until the trip is reconciled
  const {
    isPlanning,
    isClosed,
    isCancelled,
    plannable,
    canTake,
    canReconcile,
    canAddTasks
  } = tripActions.state;

  const detailsPanel = useMemo(() => {
    if (instanceQuery.isFetching && !trip.pk) {
      return <Skeleton />;
    }

    const { left, right } = tripDetailFields(trip);

    return (
      <ItemDetailsGrid>
        <DetailsTable title={t`Field Trip`} fields={left} item={trip} />
        <DetailsTable title={t`Dates and Kit`} fields={right} item={trip} />
      </ItemDetailsGrid>
    );
  }, [trip, instanceQuery]);

  const kitPanel = useMemo(() => {
    if (!trip.pk) {
      return <Skeleton />;
    }

    return (
      <Stack gap='sm'>
        {!kit?.kit_location && (
          <Alert color='blue'>{t`The trip has no kit location yet.`}</Alert>
        )}
        <TripKitTable
          trip={trip}
          lines={kit?.lines ?? []}
          editable={plannable}
          canTake={canTake}
          onChange={refreshAll}
        />
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Title order={5}>{t`Kit Contents`}</Title>
            <KitStockTable
              items={kit?.contents ?? []}
              tableName='fleet-trip-kit-contents'
            />
          </Stack>
        </Paper>
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Title order={5}>{t`Removed Components`}</Title>
            <Text size='sm' c='dimmed'>
              {t`Components removed in the field are kept here until the trip is reconciled.`}
            </Text>
            <KitStockTable
              items={kit?.removed ?? []}
              tableName='fleet-trip-kit-removed'
            />
          </Stack>
        </Paper>
      </Stack>
    );
  }, [trip, kit, plannable, canTake, refreshAll]);

  const panels: PanelType[] = useMemo(
    () => [
      {
        name: 'details',
        label: t`Trip Details`,
        icon: <IconInfoCircle />,
        content: detailsPanel
      },
      {
        name: 'tasks',
        label: t`Tasks`,
        icon: <IconListCheck />,
        content: trip.pk ? (
          <TaskTable
            params={{ trip: trip.pk }}
            tableName='fleet-trip-tasks'
            tripId={trip.pk}
            tripEditable={plannable}
            onTripChange={refreshAll}
            extraActions={
              canAddTasks
                ? [
                    <Button
                      key='add-tasks'
                      variant='outline'
                      leftSection={<IconPlus size={18} />}
                      onClick={tripActions.openAddTasks}
                    >
                      {t`Add Tasks`}
                    </Button>
                  ]
                : []
            }
          />
        ) : null
      },
      {
        name: 'kit',
        label: t`Kit`,
        icon: <IconPackages />,
        content: kitPanel
      },
      {
        name: 'reconcile',
        label: t`Reconcile`,
        icon: <IconArrowBackUp />,
        hidden: isPlanning || isCancelled,
        content: trip.pk ? (
          <TripReconcilePanel
            trip={trip}
            kit={kit}
            canReconcile={canReconcile}
            onChange={refreshAll}
          />
        ) : null
      },
      AttachmentPanel({
        model_type: ModelType.fieldtrip,
        model_id: trip.pk
      }),
      NotesPanel({
        model_type: ModelType.fieldtrip,
        model_id: trip.pk,
        has_note: !!trip.notes
      })
    ],
    [
      trip,
      kit,
      detailsPanel,
      kitPanel,
      plannable,
      canAddTasks,
      canReconcile,
      isPlanning,
      isCancelled,
      refreshAll
    ]
  );

  const actions = useMemo(
    () => [
      <PrimaryActionButton
        key='start'
        title={t`Start Trip`}
        icon='issue'
        color='green'
        hidden={!tripActions.state.canStart}
        onClick={tripActions.openStart}
      />,
      <AdminButton key='admin' model={ModelType.fieldtrip} id={trip.pk} />,
      <BarcodeActionDropdown
        key='barcode'
        model={ModelType.fieldtrip}
        pk={trip.pk}
        hash={trip?.barcode_hash}
        perm={user.hasChangeRole(UserRoles.fleet)}
      />,
      <PrintingActions
        key='print'
        modelType={ModelType.fieldtrip}
        items={[trip.pk]}
        enableReports
      />,
      <OptionsActionDropdown
        key='options'
        tooltip={t`Trip Actions`}
        actions={[
          EditItemAction({
            hidden: !tripActions.state.canEdit,
            onClick: tripActions.openEdit
          }),
          CancelItemAction({
            hidden: !tripActions.state.canCancel,
            onClick: tripActions.openCancel
          }),
          DeleteItemAction({
            hidden: !tripActions.state.canDelete,
            onClick: tripActions.openDelete
          })
        ]}
      />
    ],
    [trip, tripActions, user]
  );

  const badges = useMemo(() => {
    if (!trip.pk) {
      return [];
    }

    return [
      <StatusRenderer
        key='status'
        status={trip.status_custom_key || trip.status}
        type={ModelType.fieldtrip}
        options={{ size: 'lg' }}
      />
    ];
  }, [trip]);

  const subtitle = [trip.title, trip.vessel].filter(Boolean).join(' · ');

  return (
    <>
      {tripActions.modals}
      <InstanceDetail query={instanceQuery} requiredRole={UserRoles.fleet}>
        <Stack gap='xs'>
          <PageDetail
            title={`${t`Field Trip`}: ${trip.reference ?? ''}`}
            subtitle={subtitle}
            badges={badges}
            breadcrumbs={[
              { name: t`Fleet`, url: '/fleet/' },
              { name: t`Trips`, url: '/fleet/index/trips' }
            ]}
            lastCrumb={[
              {
                name: trip.reference,
                url: getDetailUrl(ModelType.fieldtrip, trip.pk)
              }
            ]}
            actions={actions}
            editAction={tripActions.openEdit}
            editEnabled={tripActions.state.canEdit}
          />
          {trip.pk && (
            <Paper withBorder p='sm'>
              <TripStatusSteps status={trip.status} />
            </Paper>
          )}
          {isClosed && (
            <Text size='sm' c='dimmed'>
              {t`This trip is closed: the kit was returned to stock.`}
            </Text>
          )}
          <PanelGroup
            pageKey='fleet-trip'
            panels={panels}
            instance={trip}
            reloadInstance={refreshAll}
            model={ModelType.fieldtrip}
            id={trip.pk}
          />
        </Stack>
      </InstanceDetail>
    </>
  );
}
