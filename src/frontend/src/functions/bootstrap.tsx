import * as Sentry from '@sentry/react';

/**
 * Start-up code shared by the entries of the frontend: the main UI
 * (src/main.tsx) and the Fleet Portal (src/portal/main.tsx).
 *
 * This module must stay free of imports of the entries themselves (and of
 * router.tsx): the portal imports it, and reaching src/main.tsx from the
 * portal graph would merge the two Vite entries.
 */

/**
 * Build window.INVENTREE_SETTINGS: the default server list, merged with the
 * settings already set by Django (the spa view), then the overrides.
 *
 * @param dev - running under "vite dev" (adds the localhost server)
 * @param demo - demo build (adds the demo server)
 * @param demoServer - offer the demo server in dev / demo builds
 * @param overrides - settings which always win (e.g. the portal base URL)
 */
export function loadInvenTreeSettings({
  dev,
  demo = false,
  demoServer = true,
  overrides = {}
}: {
  dev: boolean;
  demo?: boolean;
  demoServer?: boolean;
  overrides?: Record<string, any>;
}) {
  // Filter out any settings that are not defined
  const loaded_vals = (window.INVENTREE_SETTINGS || {}) as any;

  Object.keys(loaded_vals).forEach((key) => {
    if (loaded_vals[key] === undefined) {
      delete loaded_vals[key];

      // check for empty server list
    } else if (key === 'server_list' && loaded_vals[key].length === 0) {
      delete loaded_vals[key];
    }
  });

  window.INVENTREE_SETTINGS = {
    server_list: {
      ...(dev
        ? {
            'server-localhost': {
              host: 'http://localhost:8000',
              name: 'Localhost'
            }
          }
        : {}),
      ...(demoServer && (dev || demo)
        ? {
            'server-demo': {
              host: 'https://demo.inventree.org/',
              name: 'InvenTree Demo'
            }
          }
        : {}),
      'server-current': {
        host: `${window.location.origin}/`,
        name: 'Current Server'
      }
    },
    default_server: dev
      ? 'server-localhost'
      : demo
        ? 'server-demo'
        : 'server-current',
    show_server_selector: dev || demo,

    // Merge in settings that are already set via django's spa_view or for development
    ...loaded_vals,

    ...overrides
  };
}

/**
 * Start Sentry when the server provides a DSN
 */
export function initSentry() {
  if (window.INVENTREE_SETTINGS.sentry_dsn) {
    console.log('Sentry enabled');
    Sentry.init({
      dsn: window.INVENTREE_SETTINGS.sentry_dsn,
      tracesSampleRate: 1.0,
      environment: window.INVENTREE_SETTINGS.environment || 'default'
    });
  }
}
