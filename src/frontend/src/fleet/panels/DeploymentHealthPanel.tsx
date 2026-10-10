import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  List,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconCircleCheck, IconCircleX, IconRefresh } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useCallback, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { useUserState } from '../../states/UserState';
import { FleetStatus, FleetStatusBadge } from '../components/FleetBadges';
import { StreamStatusList, formatAge } from '../components/StreamStatusList';
import { DataStreamTable } from '../tables/DataStreamTable';

/**
 * Result of a "check live data" (verify) call
 */
export function VerifyResult({ result }: Readonly<{ result: any }>): ReactNode {
  return (
    <Alert
      color={result.passed ? 'green' : 'red'}
      icon={result.passed ? <IconCircleCheck /> : <IconCircleX />}
      title={
        result.passed
          ? t`Fresh data from every essential stream`
          : t`Not all essential streams report fresh data`
      }
    >
      <Stack gap={4}>
        <Text size='xs'>
          {t`Data newer than`} {formatDate(result.since, { showTime: true })}
        </Text>
        <List size='sm'>
          {(result.streams ?? []).map((stream: any) => (
            <List.Item
              key={stream.key}
              icon={
                stream.fresh ? (
                  <IconCircleCheck size={16} color='green' />
                ) : (
                  <IconCircleX size={16} color='red' />
                )
              }
            >
              {stream.name || stream.key}
              {stream.essential ? ` (${t`essential`})` : ''}:{' '}
              {formatAge(stream.last_seen)}
            </List.Item>
          ))}
        </List>
        {result.inside_geofence === false && (
          <Text size='sm' c='red'>
            {t`The device is outside its geofence`}
            {result.distance_m != null ? ` (${result.distance_m} m)` : ''}
          </Text>
        )}
        {result.inside_geofence === null && (
          <Text size='xs' c='dimmed'>
            {t`No position available`}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}

/**
 * Live health of a deployed device: streams, a "check live data" button
 * (which stores nothing), and the stream settings
 */
export function DeploymentHealthPanel({
  deployment
}: Readonly<{ deployment: any }>): ReactNode {
  const api = useApi();
  const user = useUserState();
  const queryClient = useQueryClient();

  const [checking, setChecking] = useState<boolean>(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string>('');

  const checkNow = useCallback(() => {
    setChecking(true);
    setError('');

    api
      .post(apiUrl(ApiEndpoints.fleet_deployment_verify, deployment.pk), {})
      .then((response) => {
        setResult(response.data);
        queryClient.invalidateQueries({
          queryKey: ['fleet-streams', deployment.pk]
        });
      })
      .catch((err) => {
        setResult(null);
        const data = err?.response?.data;
        setError(
          data?.non_field_errors?.join(' ') ??
            t`The live data could not be checked`
        );
      })
      .finally(() => setChecking(false));
  }, [api, deployment.pk, queryClient]);

  return (
    <Stack gap='sm'>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing='sm'>
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Group justify='space-between'>
              <Title order={5}>{t`Live Status`}</Title>
              <FleetStatusBadge
                type={FleetStatus.health}
                status={deployment.health}
              />
            </Group>
            <Text size='sm'>
              {t`Last data`}:{' '}
              {deployment.last_contact
                ? `${formatDate(deployment.last_contact, { showTime: true })} (${formatAge(deployment.last_contact)})`
                : t`Never`}
            </Text>
            <Text size='xs' c='dimmed'>
              {t`Last checked`}:{' '}
              {deployment.last_polled_at
                ? formatDate(deployment.last_polled_at, { showTime: true })
                : t`Never`}
            </Text>
            {!deployment.platform_id && (
              <Alert color='orange' p='xs'>
                {t`The device has no platform ID, so it is not monitored.`}
              </Alert>
            )}
            <StreamStatusList deploymentId={deployment.pk} />
          </Stack>
        </Paper>
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Title order={5}>{t`Check Live Data`}</Title>
            <Text size='sm' c='dimmed'>
              {t`Ask the data platform now whether every essential stream is reporting fresh data. Nothing is stored.`}
            </Text>
            <Group>
              <Button
                leftSection={<IconRefresh />}
                loading={checking}
                disabled={
                  !deployment.platform_id ||
                  !user.hasChangeRole(UserRoles.fleet)
                }
                onClick={checkNow}
              >
                {t`Check now`}
              </Button>
            </Group>
            {error && <Alert color='red'>{error}</Alert>}
            {result && <VerifyResult result={result} />}
          </Stack>
        </Paper>
      </SimpleGrid>
      <Title order={5}>{t`Stream Settings`}</Title>
      <DataStreamTable deploymentId={deployment.pk} />
    </Stack>
  );
}
