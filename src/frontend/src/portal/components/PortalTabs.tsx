import { Loader, Tabs } from '@mantine/core';
import { type ReactNode, Suspense, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { Boundary } from '@lib/components/Boundary';
import type { PanelType } from '@lib/types/Panel';

/**
 * Tabs of a portal detail screen. Accepts the same panel objects as the
 * PanelGroup of the main UI (e.g. AttachmentPanel, NotesPanel); the selected
 * tab is kept in the URL (?tab=...) so "back" returns to it. The tab bar
 * scrolls sideways on narrow screens.
 */
export function PortalTabs({
  panels,
  defaultTab
}: Readonly<{
  panels: PanelType[];
  defaultTab?: string;
}>): ReactNode {
  const [searchParams, setSearchParams] = useSearchParams();

  const visible = useMemo(
    () => panels.filter((panel) => !panel.hidden && !panel.disabled),
    [panels]
  );

  const requested = searchParams.get('tab');
  const fallback = defaultTab ?? visible[0]?.name;
  const active = visible.some((panel) => panel.name == requested)
    ? requested
    : fallback;

  return (
    <Tabs
      value={active}
      keepMounted={false}
      onChange={(value) => {
        if (value) {
          const params = new URLSearchParams(searchParams);
          params.set('tab', value);
          setSearchParams(params, { replace: true });
        }
      }}
    >
      <Tabs.List
        style={{ flexWrap: 'nowrap', overflowX: 'auto', overflowY: 'hidden' }}
      >
        {visible.map((panel) => (
          <Tabs.Tab
            key={panel.name}
            value={panel.name}
            leftSection={panel.icon}
            aria-label={`portal-tab-${panel.name}`}
            style={{ flexShrink: 0 }}
          >
            {panel.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {visible.map((panel) => (
        <Tabs.Panel key={panel.name} value={panel.name} pt='sm'>
          <Boundary label={`portal-tab-${panel.name}`}>
            <Suspense fallback={<Loader />}>{panel.content}</Suspense>
          </Boundary>
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}
