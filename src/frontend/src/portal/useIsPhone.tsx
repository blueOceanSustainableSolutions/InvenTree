import { useMediaQuery } from '@mantine/hooks';

/** Phones: below the Mantine "sm" breakpoint (48em = 768px) */
export const PHONE_QUERY = '(max-width: 47.99em)';

/** Is the portal shown at phone width? */
export function useIsPhone(): boolean {
  return (
    useMediaQuery(PHONE_QUERY, window.matchMedia?.(PHONE_QUERY).matches, {
      getInitialValueInEffect: false
    }) ?? false
  );
}
