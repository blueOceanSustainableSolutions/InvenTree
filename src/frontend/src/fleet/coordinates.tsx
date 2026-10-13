import { t } from '@lingui/core/macro';
import { Badge, Group, Text, Tooltip } from '@mantine/core';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { CopyButton } from '@lib/components/CopyButton';

/**
 * Coordinates: entered as DD, DDM or DMS, stored by the server as signed
 * decimal degrees (10 decimal places), shown in the format the user picks.
 * The parser mirrors fleet/geo.py parse_coordinate on the server.
 */

export type CoordinateAxis = 'latitude' | 'longitude';

/** DD: decimal degrees, DDM: degrees + decimal minutes, DMS: degrees + minutes + seconds */
export type CoordinateFormat = 'DD' | 'DDM' | 'DMS';

export const COORDINATE_FORMATS: CoordinateFormat[] = ['DD', 'DDM', 'DMS'];

const LIMITS: Record<CoordinateAxis, number> = { latitude: 90, longitude: 180 };

// Hemisphere letters and their sign ('O' is Oeste, west)
const HEMISPHERES: Record<CoordinateAxis, Record<string, number>> = {
  latitude: { N: 1, S: -1 },
  longitude: { E: 1, W: -1, O: -1 }
};

const SEPARATORS = /[\s:'"°º˚′’″”]+/;
const NUMBER = /^\d+([.,]\d+)?$/;

export function coordinateFormatLabel(format: CoordinateFormat): string {
  switch (format) {
    case 'DD':
      return t`Decimal degrees`;
    case 'DDM':
      return t`Degrees and decimal minutes`;
    case 'DMS':
      return t`Degrees, minutes and seconds`;
  }
}

export type ParsedCoordinate = {
  value: number | null;
  error?: string;
};

/**
 * Parse a coordinate given as DD (38.7222, -9.14, 38.7222 N),
 * DDM (38 43.335, 38°43.335'N) or DMS (38 43 20.1, 38°43'20.1"N).
 * Empty text gives a null value.
 */
export function parseCoordinate(
  input: string,
  axis: CoordinateAxis
): ParsedCoordinate {
  let text = input.trim().toUpperCase();

  if (!text) {
    return { value: null };
  }

  const hemispheres = HEMISPHERES[axis];
  const letters = Object.keys(hemispheres).join('/');
  let sign = 1;
  let hemisphere: string | null = null;

  if (/^[A-Z]/.test(text)) {
    hemisphere = text[0];
    text = text.slice(1);
  } else if (/[A-Z]$/.test(text)) {
    hemisphere = text[text.length - 1];
    text = text.slice(0, -1);
  }

  if (hemisphere != null) {
    if (!(hemisphere in hemispheres)) {
      return { value: null, error: t`Hemisphere must be ${letters}` };
    }
    sign = hemispheres[hemisphere];
  }

  text = text.trim();

  if (text.startsWith('-') || text.startsWith('+')) {
    if (hemisphere != null && text.startsWith('-')) {
      return {
        value: null,
        error: t`Use either a minus sign or a hemisphere letter`
      };
    }
    if (text.startsWith('-')) {
      sign = -sign;
    }
    text = text.slice(1);
  }

  const parts = text.split(SEPARATORS).filter((part) => part !== '');

  if (
    parts.length < 1 ||
    parts.length > 3 ||
    !parts.every((part) => NUMBER.test(part))
  ) {
    return {
      value: null,
      error: t`Enter degrees, degrees and minutes, or degrees, minutes and seconds`
    };
  }

  if (parts.slice(0, -1).some((part) => !/^\d+$/.test(part))) {
    return { value: null, error: t`Only the last number can have decimals` };
  }

  const numbers = parts.map((part) => Number(part.replace(',', '.')));

  if (numbers.slice(1).some((n) => n >= 60)) {
    return { value: null, error: t`Minutes and seconds must be below 60` };
  }

  const degrees =
    numbers[0] + (numbers[1] ?? 0) / 60 + (numbers[2] ?? 0) / 3600;
  const limit = LIMITS[axis];

  if (degrees > limit) {
    return {
      value: null,
      error:
        axis == 'latitude'
          ? t`Latitude must be between -90 and 90`
          : t`Longitude must be between -180 and 180`
    };
  }

  // Same precision as the server (10 decimal places), no negative zero
  return { value: Number((sign * degrees).toFixed(10)) + 0 };
}

/** A fixed-point number with trailing zeros removed */
function trimmed(value: number, digits: number): string {
  return value.toFixed(digits).replace(/\.?0+$/, '');
}

/**
 * Format a coordinate (decimal degrees, as number or string) in a format.
 * DD keeps 8 decimals, DDM 6 decimals of minutes, DMS 4 decimals of seconds
 * (all finer than 1 mm), with trailing zeros removed.
 */
export function formatCoordinate(
  value: number | string | null | undefined,
  axis: CoordinateAxis,
  format: CoordinateFormat
): string {
  if (value == null || value === '') {
    return '';
  }

  const degrees = Number(value);

  if (!Number.isFinite(degrees)) {
    return String(value);
  }

  if (format == 'DD') {
    return trimmed(degrees, 8);
  }

  const hemisphere =
    axis == 'latitude' ? (degrees < 0 ? 'S' : 'N') : degrees < 0 ? 'W' : 'E';
  const absolute = Math.abs(degrees);

  if (format == 'DDM') {
    // Round in whole units of the last digit, so 59.9999999' carries over
    const units = Math.round(absolute * 60 * 1e6);
    const whole = Math.floor(units / 60e6);
    const minutes = (units - whole * 60e6) / 1e6;

    return `${whole}° ${trimmed(minutes, 6)}' ${hemisphere}`;
  }

  const units = Math.round(absolute * 3600 * 1e4);
  const whole = Math.floor(units / 3600e4);
  const minutes = Math.floor((units - whole * 3600e4) / 60e4);
  const seconds = (units - whole * 3600e4 - minutes * 60e4) / 1e4;

  return `${whole}° ${minutes}' ${trimmed(seconds, 4)}" ${hemisphere}`;
}

/** A latitude and longitude pair, in a format */
export function formatPosition(
  latitude: number | string | null | undefined,
  longitude: number | string | null | undefined,
  format: CoordinateFormat
): string {
  if (latitude == null || longitude == null) {
    return '';
  }

  return `${formatCoordinate(latitude, 'latitude', format)}, ${formatCoordinate(
    longitude,
    'longitude',
    format
  )}`;
}

interface CoordinateFormatState {
  format: CoordinateFormat;
  setFormat: (format: CoordinateFormat) => void;
  nextFormat: () => void;
}

/** The format the user picked to see coordinates in (kept in the browser) */
export const useCoordinateFormat = create<CoordinateFormatState>()(
  persist(
    (set, get) => ({
      format: 'DD',
      setFormat: (format: CoordinateFormat) => set({ format }),
      nextFormat: () => {
        const index = COORDINATE_FORMATS.indexOf(get().format);
        set({
          format: COORDINATE_FORMATS[(index + 1) % COORDINATE_FORMATS.length]
        });
      }
    }),
    { name: 'fleet-coordinate-format' }
  )
);

/** A badge with the current format: click it to see the next format */
export function CoordinateFormatBadge() {
  const { format, nextFormat } = useCoordinateFormat();

  return (
    <Tooltip
      label={t`${coordinateFormatLabel(format)}: click to change the format`}
      withArrow
    >
      <Badge
        size='xs'
        variant='light'
        style={{ cursor: 'pointer' }}
        aria-label='coordinate-format'
        onClick={(event: any) => {
          event.stopPropagation();
          nextFormat();
        }}
      >
        {format}
      </Badge>
    </Tooltip>
  );
}

/**
 * A latitude or longitude in the current format, with a format badge and a
 * copy button (copies the value as shown)
 */
export function CoordinateValue({
  value,
  axis
}: Readonly<{
  value: number | string | null | undefined;
  axis: CoordinateAxis;
}>) {
  const format = useCoordinateFormat((state) => state.format);
  const text = formatCoordinate(value, axis, format);

  return (
    <Group gap='xs' wrap='nowrap'>
      <Text size='sm'>{text}</Text>
      <CoordinateFormatBadge />
      <CopyButton value={text} size='xs' />
    </Group>
  );
}

/**
 * A latitude and longitude pair in the current format (with a format badge
 * unless compact, for tables)
 */
export function PositionValue({
  latitude,
  longitude,
  suffix,
  compact
}: Readonly<{
  latitude: number | string | null | undefined;
  longitude: number | string | null | undefined;
  suffix?: string;
  compact?: boolean;
}>) {
  const format = useCoordinateFormat((state) => state.format);
  const text = `${formatPosition(latitude, longitude, format)}${suffix ?? ''}`;

  if (compact) {
    return <Text size='sm'>{text}</Text>;
  }

  return (
    <Group gap='xs' wrap='nowrap'>
      <Text size='sm'>{text}</Text>
      <CoordinateFormatBadge />
    </Group>
  );
}

/** A latitude or longitude in the current format, as plain text (for tables) */
export function CoordinateText({
  value,
  axis
}: Readonly<{
  value: number | string | null | undefined;
  axis: CoordinateAxis;
}>) {
  const format = useCoordinateFormat((state) => state.format);

  return <Text size='sm'>{formatCoordinate(value, axis, format)}</Text>;
}
