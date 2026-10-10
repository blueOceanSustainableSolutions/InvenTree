import '@mantine/carousel/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/spotlight/styles.css';
import 'mantine-contextmenu/styles.css';
import 'mantine-datatable/styles.css';

import * as React from 'react';
import * as ReactDOMClient from 'react-dom/client';

import '../styles/overrides.css';

import { initSentry, loadInvenTreeSettings } from '../functions/bootstrap';
import { loadWindowGlobals } from '../window';

/**
 * Entry point of the Fleet Portal (second Vite entry: fleet.html).
 *
 * The portal shares the backend, the login and the frontend code with the
 * main UI, but has its own router (base path /fleet/) and a layout which
 * works on phones. Django serves it at /fleet/ with the settings of the main
 * UI, except `base_url` ('fleet') and `main_base_url` (the main UI path).
 */

// Running in dev mode (i.e. vite)
const IS_DEV = import.meta.env.DEV;

// Settings from django's fleet view (shared with the main UI entry), and
// Sentry. The portal always lives at /fleet/ (also under "vite dev").
loadInvenTreeSettings({
  dev: IS_DEV,
  demoServer: false,
  overrides: { base_url: 'fleet' }
});
initSentry();

// Loaded after the settings above: shared modules read them when they load
const PortalView = React.lazy(() => import('./PortalView'));

ReactDOMClient.createRoot(
  document.getElementById('root') as HTMLElement
).render(
  <React.StrictMode>
    <React.Suspense fallback={null}>
      <PortalView />
    </React.Suspense>
  </React.StrictMode>
);

// Load globals onto the window object (as the main UI), so that they can be
// accessed by plugins without requiring direct imports
loadWindowGlobals();
