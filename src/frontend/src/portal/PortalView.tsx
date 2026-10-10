// Portal styles: imported by this lazy module (not by main.tsx) so that the
// build lists them with this chunk, which Vite loads together with it.
// Django renders the entry script only, not the stylesheets of the entry.
import './portal.css';

import { useEffect } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { useShallow } from 'zustand/react/shallow';

import { getBaseUrl } from '@lib/functions/Navigation';
import { api, queryClient, setApiDefaults } from '../App';
import { ApiProvider } from '../contexts/ApiContext';
import { ThemeContext } from '../contexts/ThemeContext';
import { defaultHostList } from '../defaults/defaultHostList';
import { useLocalState } from '../states/LocalState';
import { PortalRoutes } from './PortalRoutes';

// Set up the API client before the first page asks for data. This module is
// loaded lazily, after main.tsx has set window.INVENTREE_SETTINGS.
try {
  setApiDefaults();
} catch (e) {
  console.error(e);
}

/**
 * The Fleet Portal application: the same API client, theme and language as
 * the main UI, with the portal routes under /fleet/.
 *
 * Unlike the main UI (MainView), there is no "mobile viewport" blocker:
 * the portal is made for phones as well.
 */
export default function PortalView() {
  const [hostList] = useLocalState(useShallow((state) => [state.hostList]));

  useEffect(() => {
    if (Object.keys(hostList).length === 0) {
      useLocalState.setState({ hostList: defaultHostList });
    }
  }, [hostList]);

  return (
    <ApiProvider client={queryClient} api={api}>
      <ThemeContext>
        <BrowserRouter basename={getBaseUrl()}>
          <PortalRoutes />
        </BrowserRouter>
      </ThemeContext>
    </ApiProvider>
  );
}
