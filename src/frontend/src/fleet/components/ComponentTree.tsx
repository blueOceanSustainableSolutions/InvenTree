import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Group,
  Loader,
  Stack,
  Text,
  UnstyledButton
} from '@mantine/core';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';

/** Load the components installed directly in a stock item */
function useComponents(unitId: number, enabled = true) {
  const api = useApi();

  return useQuery({
    queryKey: ['fleet-component-tree', unitId],
    enabled: enabled && !!unitId,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.stock_components, unitId))
        .then((response) => response.data?.components ?? [])
  });
}

/** One installed component; components with their own parts can be expanded */
function ComponentNode({
  component,
  depth
}: Readonly<{ component: any; depth: number }>): ReactNode {
  const [opened, setOpened] = useState<boolean>(false);

  const label = component.serial
    ? `${component.part} #${component.serial}`
    : `${component.quantity} x ${component.part}`;

  return (
    <Stack gap={2}>
      <Group gap='xs' wrap='nowrap' pl={depth * 16}>
        {component.has_components ? (
          <ActionIcon
            variant='subtle'
            size='sm'
            aria-label={`expand-component-${component.pk}`}
            onClick={() => setOpened(!opened)}
          >
            {opened ? <IconChevronDown /> : <IconChevronRight />}
          </ActionIcon>
        ) : (
          <span style={{ width: 22, flexShrink: 0 }} />
        )}
        <UnstyledButton
          onClick={() => setOpened(!opened)}
          disabled={!component.has_components}
          style={{ minWidth: 0 }}
        >
          <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
            {label}
            {component.batch ? ` (${component.batch})` : ''}
          </Text>
        </UnstyledButton>
        {component.status_text && (
          <Badge size='xs' variant='light' style={{ flexShrink: 0 }}>
            {component.status_text}
          </Badge>
        )}
      </Group>
      {opened && <ComponentLevel unitId={component.pk} depth={depth + 1} />}
    </Stack>
  );
}

/** The components of one unit (one level of the tree) */
function ComponentLevel({
  unitId,
  depth
}: Readonly<{ unitId: number; depth: number }>): ReactNode {
  const query = useComponents(unitId);

  if (query.isLoading) {
    return <Loader size='xs' ml={depth * 16} />;
  }

  if (query.isError) {
    return (
      <Text size='sm' c='red' pl={depth * 16}>
        {t`The components could not be loaded`}
      </Text>
    );
  }

  const components: any[] = query.data ?? [];

  if (components.length == 0) {
    return (
      <Text size='sm' c='dimmed' pl={depth * 16}>
        {t`No installed components`}
      </Text>
    );
  }

  return (
    <Stack gap={2}>
      {components.map((component: any) => (
        <ComponentNode key={component.pk} component={component} depth={depth} />
      ))}
    </Stack>
  );
}

/**
 * Read-only tree of the components installed in a device
 * (`GET stock/<pk>/components/`, one level loaded at a time).
 * Changing components is done in a maintenance task.
 */
export function ComponentTree({
  deviceId
}: Readonly<{ deviceId: number }>): ReactNode {
  return <ComponentLevel unitId={deviceId} depth={0} />;
}
