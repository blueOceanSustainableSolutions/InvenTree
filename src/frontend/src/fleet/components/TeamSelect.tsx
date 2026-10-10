import { t } from '@lingui/core/macro';
import { MultiSelect } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import {
  type ReactNode,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore
} from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';

/**
 * The backend error of the `team` field of a trip form.
 *
 * `team` is not an API form field, so the form cannot show its error under
 * the control. The trip forms put the error here (onFormError) and the
 * TeamSelect shows it. A small store (not a field property) so that setting
 * the error does not rebuild the form fields.
 */
export type TeamErrorStore = {
  get: () => string | undefined;
  set: (error?: string) => void;
  subscribe: (listener: () => void) => () => void;
};

export function createTeamErrorStore(): TeamErrorStore {
  let current: string | undefined = undefined;
  const listeners = new Set<() => void>();

  return {
    get: () => current,
    set: (error?: string) => {
      if (error !== current) {
        current = error;
        listeners.forEach((listener) => listener());
      }
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    }
  };
}

/** The error messages of the `team` field in an API error response */
export function teamErrorMessage(error: any): string | undefined {
  const errors = error?.response?.data?.team;

  if (!errors) {
    return undefined;
  }

  const flatten = (value: any): string[] => {
    if (Array.isArray(value)) {
      return value.flatMap(flatten);
    }

    if (value && typeof value === 'object') {
      return Object.values(value).flatMap(flatten);
    }

    return [String(value)];
  };

  return flatten(errors).join(', ');
}

const noSubscribe = () => () => {};
const noError = () => undefined;

/**
 * Pick the team of a field trip (several users).
 *
 * The API form has no field type for a list of related objects, so the trip
 * forms show this below the responsible field and submit its value. The
 * selection is kept here, so picking users does not rebuild the form fields.
 * Backend errors of the field are shown under the control (`errors`).
 */
export function TeamSelect({
  defaultValue,
  onChange,
  errors
}: Readonly<{
  defaultValue: number[];
  onChange: (value: number[]) => void;
  errors?: TeamErrorStore;
}>): ReactNode {
  const api = useApi();

  const error = useSyncExternalStore(
    errors?.subscribe ?? noSubscribe,
    errors?.get ?? noError
  );

  const [value, setValue] = useState<number[]>(defaultValue ?? []);

  useEffect(() => {
    setValue(defaultValue ?? []);
  }, [defaultValue]);

  const usersQuery = useQuery({
    queryKey: ['fleet-team-users'],
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.user_list), { params: { is_active: true } })
        .then((response) => response.data?.results ?? response.data ?? [])
  });

  const options = useMemo(
    () =>
      (usersQuery.data ?? []).map((user: any) => {
        const name = [user.first_name, user.last_name]
          .filter(Boolean)
          .join(' ');

        return {
          value: String(user.pk),
          label: name ? `${user.username} (${name})` : user.username
        };
      }),
    [usersQuery.data]
  );

  return (
    <MultiSelect
      mt='xs'
      label={t`Team`}
      description={t`Technicians who go on the trip`}
      data={options}
      value={value.map((pk) => String(pk))}
      error={error}
      onChange={(selected: string[]) => {
        const pks = selected.map((pk) => Number(pk));
        setValue(pks);
        errors?.set(undefined);
        onChange(pks);
      }}
      searchable
      clearable
      aria-label='trip-team'
    />
  );
}
