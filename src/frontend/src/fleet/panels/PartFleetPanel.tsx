import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Skeleton, Stack, Table } from '@mantine/core';
import { IconBuildingLighthouse } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import { useApi } from '../../contexts/ApiContext';
import { useDeviceTypeFields } from '../../forms/FleetForms';
import {
  useCreateApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';

/**
 * "Fleet device" panel of a part: create or edit its fleet device type
 */
export function PartFleetPanel({ part }: Readonly<{ part: any }>): ReactNode {
  const api = useApi();
  const user = useUserState();
  const navigate = useNavigate();

  const query = useQuery({
    queryKey: ['fleet-part-device-type', part.pk],
    enabled: !!part.pk,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_device_type_list), {
          params: { part: part.pk }
        })
        .then((res) => res.data?.[0] ?? null)
  });

  const deviceType = query.data;
  const fields = useDeviceTypeFields({ partId: part.pk });

  const createDeviceType = useCreateApiFormModal({
    url: ApiEndpoints.fleet_device_type_list,
    title: t`Mark as Fleet Device`,
    fields: fields,
    initialData: { part: part.pk },
    onFormSuccess: () => query.refetch()
  });

  const editDeviceType = useEditApiFormModal({
    url: ApiEndpoints.fleet_device_type_list,
    pk: deviceType?.pk,
    title: t`Edit Device Type`,
    fields: fields,
    onFormSuccess: () => query.refetch()
  });

  if (query.isLoading) {
    return <Skeleton />;
  }

  if (!deviceType) {
    return (
      <>
        {createDeviceType.modal}
        <Alert color='blue' title={t`Not a fleet device`}>
          <Stack gap='xs'>
            {t`Mark this part as a fleet device: issuing a build order for it adds a deployment to the fleet pipeline.`}
            {user.hasAddRole(UserRoles.fleet) && (
              <Group>
                <Button
                  leftSection={<IconBuildingLighthouse />}
                  onClick={() => createDeviceType.open()}
                >
                  {t`Mark as Fleet Device`}
                </Button>
              </Group>
            )}
          </Stack>
        </Alert>
      </>
    );
  }

  return (
    <>
      {editDeviceType.modal}
      <Stack gap='xs'>
        <Table>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td>{t`PM Interval (days)`}</Table.Td>
              <Table.Td>{deviceType.pm_interval_days}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Data Streams`}</Table.Td>
              <Table.Td>{deviceType.stream_count}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Deployed`}</Table.Td>
              <Table.Td>{deviceType.deployment_count}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>{t`Active`}</Table.Td>
              <Table.Td>{deviceType.active ? t`Yes` : t`No`}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Group>
          <Button
            variant='light'
            onClick={() =>
              navigate(getDetailUrl(ModelType.fleetdevicetype, deviceType.pk))
            }
          >
            {t`Open Device Type`}
          </Button>
          {user.hasChangeRole(UserRoles.fleet) && (
            <Button variant='light' onClick={() => editDeviceType.open()}>
              {t`Edit`}
            </Button>
          )}
        </Group>
      </Stack>
    </>
  );
}
