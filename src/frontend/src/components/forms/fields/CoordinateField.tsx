import { t } from '@lingui/core/macro';
import { Stack, Text, TextInput } from '@mantine/core';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { FieldValues, UseControllerReturn } from 'react-hook-form';

import {
  COORDINATE_FORMATS,
  type CoordinateAxis,
  formatCoordinate,
  parseCoordinate,
  useCoordinateFormat
} from '../../../fleet/coordinates';

/** Whether two coordinate values (number, decimal string or null) are the same */
function sameValue(a: any, b: any): boolean {
  if (a === b) {
    return true;
  }

  if (a == null || a === '' || b == null || b === '') {
    return (a == null || a === '') && (b == null || b === '');
  }

  return Number(a) === Number(b);
}

/**
 * A latitude or longitude input. Accepts decimal degrees, degrees and
 * decimal minutes, or degrees, minutes and seconds; shows the value in all
 * three formats below the input; sends decimal degrees to the server.
 */
export default function CoordinateField({
  controller,
  fieldName,
  definition,
  onChange
}: Readonly<{
  controller: UseControllerReturn<FieldValues, any>;
  definition: any;
  fieldName: string;
  onChange: (value: any) => void;
}>) {
  const fieldId = useId();
  const {
    field,
    fieldState: { error }
  } = controller;

  const axis: CoordinateAxis =
    definition.axis ??
    (fieldName.toLowerCase().includes('lon') ? 'longitude' : 'latitude');

  const format = useCoordinateFormat((state) => state.format);

  const [text, setText] = useState<string>(() =>
    formatCoordinate(field.value, axis, format)
  );

  // The last value sent to the form (to tell it from a value set elsewhere)
  const sent = useRef<any>(field.value);

  // A value set elsewhere (initial data, a default position) replaces the text
  useEffect(() => {
    if (!sameValue(field.value, sent.current)) {
      sent.current = field.value;
      setText(formatCoordinate(field.value, axis, format));
    }
  }, [field.value]);

  const parsed = useMemo(() => parseCoordinate(text, axis), [text, axis]);

  const preview = useMemo(() => {
    if (parsed.value == null) {
      return null;
    }

    return COORDINATE_FORMATS.map((f) =>
      formatCoordinate(parsed.value, axis, f)
    ).join('  ·  ');
  }, [parsed.value, axis]);

  return (
    <TextInput
      id={fieldId}
      ref={field.ref}
      radius='sm'
      aria-label={`coordinate-field-${fieldName}`}
      label={definition.label}
      required={definition.required}
      disabled={definition.disabled}
      placeholder={
        definition.placeholder ??
        (axis == 'latitude' ? `38.722256, 38 43.3354 N, 38 43 20.12 N` : `-9.1393, 9 8.358 W, 9 8 21.5 W`)
      }
      description={
        <Stack gap={0}>
          {definition.description && <Text size='xs'>{definition.description}</Text>}
          <Text size='xs' c={preview ? 'blue' : 'dimmed'}>
            {preview ?? t`Decimal degrees, degrees and minutes, or degrees, minutes and seconds`}
          </Text>
        </Stack>
      }
      value={text}
      error={parsed.error ?? definition.error ?? error?.message}
      onChange={(event) => {
        const value = event.currentTarget.value;
        const result = parseCoordinate(value, axis);

        setText(value);

        // An invalid text is sent as typed, so the server rejects it too
        const out =
          result.error != null
            ? value
            : result.value == null
              ? null
              : result.value.toFixed(10);

        sent.current = out;
        onChange(out);
      }}
    />
  );
}
