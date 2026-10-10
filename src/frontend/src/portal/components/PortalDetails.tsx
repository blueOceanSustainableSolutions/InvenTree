import { Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { DetailsField } from '../../components/details/Details';
import { formatDate } from '../../defaults/formatters';

/** One label / value pair of a details card */
export function Detail({
  label,
  value
}: Readonly<{ label: string; value?: ReactNode }>): ReactNode {
  if (value === undefined || value === null || value === '') {
    return null;
  }

  return (
    <Stack gap={0}>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      <Text size='sm' component='div' style={{ overflowWrap: 'anywhere' }}>
        {value}
      </Text>
    </Stack>
  );
}

/**
 * The value of a detail field (the shared definitions of
 * fleet/details/FleetDetailFields), as shown by a portal details card:
 * the value formatter, else the formatted date, else the raw value.
 *
 * Link fields need a value formatter in the portal (the portal links to its
 * own pages, or to the main UI).
 */
function detailValue(field: DetailsField, item: any): ReactNode {
  if (field.hidden) {
    return undefined;
  }

  if (field.value_formatter) {
    return field.value_formatter();
  }

  const value = item?.[field.name];

  if (value === undefined || value === null || value === '') {
    return undefined;
  }

  if (field.type == 'date') {
    return formatDate(value, { showTime: field.showTime });
  }

  return value;
}

/** The fields of a portal details card, rendered with `Detail` */
export function PortalDetailFields({
  fields,
  item
}: Readonly<{ fields: DetailsField[]; item: any }>): ReactNode {
  return (
    <>
      {fields.map((field) => (
        <Detail
          key={field.name}
          label={field.label ?? field.name}
          value={detailValue(field, item)}
        />
      ))}
    </>
  );
}
