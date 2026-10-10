import '@mantine/carousel/styles.css';
import '@mantine/charts/styles.css';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/spotlight/styles.css';
import 'mantine-contextmenu/styles.css';
import 'mantine-datatable/styles.css';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

import type * as LinguiCore from '@lingui/core';
import type * as LinguiReact from '@lingui/react';
// Global types to be exported for use in plugins
import type * as MantineCore from '@mantine/core';
import type * as MantineNotifications from '@mantine/notifications';
import * as React from 'react';
import type * as ReactDOM from 'react-dom';
import * as ReactDOMClient from 'react-dom/client';

import './styles/overrides.css';

import { getBaseUrl } from '@lib/functions/Navigation';
import type { HostList } from '@lib/types/Server';
import { initSentry, loadInvenTreeSettings } from './functions/bootstrap';
import MainView from './views/MainView';
import { loadWindowGlobals } from './window';

// define settings
declare global {
  interface Window {
    INVENTREE_SETTINGS: {
      server_list: HostList;
      default_server: string;
      show_server_selector: boolean;
      base_url?: string;
      api_host?: string;
      sentry_dsn?: string;
      environment?: string;
      mobile_mode?: 'default' | 'allow-ignore' | 'allow-always';
    };
    react: typeof React;
    React: typeof React;
    ReactDOM: typeof ReactDOM;
    ReactDOMClient: typeof ReactDOMClient;
    MantineCore: typeof MantineCore;
    MantineNotifications: typeof MantineNotifications;
    LinguiCore: typeof LinguiCore;
    LinguiReact: typeof LinguiReact;
  }
}

// Running in dev mode (i.e. vite)
export const IS_DEV = import.meta.env.DEV;
export const IS_DEMO = import.meta.env.VITE_DEMO === 'true';
export const IS_DEV_OR_DEMO = IS_DEV || IS_DEMO;

// Settings from django's spa_view (or for development), and Sentry
loadInvenTreeSettings({ dev: IS_DEV, demo: IS_DEMO });
initSentry();

// Redirect to base url if on /
if (window.location.pathname === '/') {
  window.location.replace(`/${getBaseUrl()}`);
}

ReactDOMClient.createRoot(
  document.getElementById('root') as HTMLElement
).render(
  <React.StrictMode>
    <MainView />
  </React.StrictMode>
);

// Load globals onto the window object, so that they can be accessed by plugins without requiring direct imports
loadWindowGlobals();
