import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  AppShell,
  Group,
  Menu,
  NavLink,
  Stack,
  Text,
  Title,
  UnstyledButton
} from '@mantine/core';
import {
  IconBuildingLighthouse,
  IconDots,
  IconExternalLink,
  IconLogout,
  IconUserCircle
} from '@tabler/icons-react';
import { type ReactNode, useMemo } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { Boundary } from '@lib/components/Boundary';
import { UserRoles } from '@lib/enums/Roles';
import PermissionDenied from '../components/errors/PermissionDenied';
import { ProtectedRoute } from '../components/nav/Layout';
import { useUserState } from '../states/UserState';
import {
  type PortalNavItem,
  isActivePath,
  mainUiUrl,
  portalNavItems
} from './navigation';
import { useIsPhone } from './useIsPhone';

/** User menu: name, link to the main InvenTree UI, log out */
function UserMenu(): ReactNode {
  const user = useUserState();
  const navigate = useNavigate();

  return (
    <Menu position='bottom-end' withinPortal>
      <Menu.Target>
        <ActionIcon
          variant='subtle'
          size='xl'
          aria-label='portal-user-menu'
          title={user.username()}
        >
          <IconUserCircle />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>{user.username()}</Menu.Label>
        <Menu.Item
          component='a'
          href={mainUiUrl('/home')}
          leftSection={<IconExternalLink size={18} />}
        >
          {t`Open InvenTree`}
        </Menu.Item>
        <Menu.Item
          color='red'
          leftSection={<IconLogout size={18} />}
          onClick={() => navigate('/logout')}
        >
          {t`Log out`}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

/** Side navigation (desktop and tablet) */
function SideNav({ items }: Readonly<{ items: PortalNavItem[] }>): ReactNode {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Stack gap={2}>
      {items.map((item) => (
        <NavLink
          key={item.path}
          label={item.label}
          leftSection={item.icon}
          active={isActivePath(item.path, location.pathname)}
          aria-label={`portal-nav-${item.path}`}
          onClick={() => navigate(item.path)}
        />
      ))}
      <NavLink
        component='a'
        href={mainUiUrl('/home')}
        label={t`Open InvenTree`}
        leftSection={<IconExternalLink />}
      />
    </Stack>
  );
}

/** One button of the bottom tab bar */
function TabButton({
  label,
  icon,
  active,
  ariaLabel,
  onClick
}: Readonly<{
  label: string;
  icon: ReactNode;
  active: boolean;
  ariaLabel: string;
  onClick?: () => void;
}>): ReactNode {
  return (
    <UnstyledButton
      className='fleet-portal-tab'
      data-active={active || undefined}
      aria-label={ariaLabel}
      onClick={onClick}
    >
      <Stack gap={0} align='center'>
        {icon}
        <Text size='xs' lineClamp={1}>
          {label}
        </Text>
      </Stack>
    </UnstyledButton>
  );
}

/** Bottom tab bar (phones): the main screens, the others under "More" */
function BottomTabs({
  items
}: Readonly<{ items: PortalNavItem[] }>): ReactNode {
  const location = useLocation();
  const navigate = useNavigate();

  const primary = items.filter((item) => item.primary);
  const more = items.filter((item) => !item.primary);
  const moreActive = more.some((item) =>
    isActivePath(item.path, location.pathname)
  );

  return (
    <Group gap={0} h='100%' wrap='nowrap' grow>
      {primary.map((item) => (
        <TabButton
          key={item.path}
          label={item.label}
          icon={item.icon}
          active={isActivePath(item.path, location.pathname)}
          ariaLabel={`portal-tab-${item.path}`}
          onClick={() => navigate(item.path)}
        />
      ))}
      <Menu position='top-end' withinPortal>
        <Menu.Target>
          <div>
            <TabButton
              label={t`More`}
              icon={<IconDots />}
              active={moreActive}
              ariaLabel='portal-tab-more'
            />
          </div>
        </Menu.Target>
        <Menu.Dropdown>
          {more.map((item) => (
            <Menu.Item
              key={item.path}
              leftSection={item.icon}
              onClick={() => navigate(item.path)}
            >
              {item.label}
            </Menu.Item>
          ))}
          <Menu.Divider />
          <Menu.Item
            component='a'
            href={mainUiUrl('/home')}
            leftSection={<IconExternalLink />}
          >
            {t`Open InvenTree`}
          </Menu.Item>
          <Menu.Item
            color='red'
            leftSection={<IconLogout />}
            onClick={() => navigate('/logout')}
          >
            {t`Log out`}
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

/**
 * The portal frame: a header, a side navigation on desktop, and a bottom tab
 * bar with large touch targets on phones (< 768 px).
 */
function PortalShell(): ReactNode {
  const user = useUserState();
  const navigate = useNavigate();
  const isPhone = useIsPhone();

  const items = useMemo(() => portalNavItems(), []);

  const allowed = user.hasViewRole(UserRoles.fleet);

  return (
    <AppShell
      className='fleet-portal'
      header={{ height: 56 }}
      // Phones: bottom tab bar only; larger screens: side navigation only
      navbar={
        isPhone
          ? undefined
          : { width: 220, breakpoint: 'sm', collapsed: { mobile: true } }
      }
      footer={isPhone ? { height: 64 } : undefined}
      padding='sm'
    >
      <AppShell.Header>
        <Group h='100%' px='sm' justify='space-between' wrap='nowrap'>
          <Group gap='xs' wrap='nowrap'>
            <UnstyledButton
              aria-label='portal-home'
              onClick={() => navigate('/')}
            >
              <Group gap='xs' wrap='nowrap'>
                <IconBuildingLighthouse />
                <Title order={4}>{t`Fleet`}</Title>
              </Group>
            </UnstyledButton>
          </Group>
          <UserMenu />
        </Group>
      </AppShell.Header>
      {!isPhone && (
        <AppShell.Navbar p='xs'>
          <SideNav items={items} />
        </AppShell.Navbar>
      )}
      <AppShell.Main>
        <Boundary label='fleet-portal'>
          {allowed ? <Outlet /> : <PermissionDenied />}
        </Boundary>
      </AppShell.Main>
      {isPhone && (
        <AppShell.Footer>
          <BottomTabs items={items} />
        </AppShell.Footer>
      )}
    </AppShell>
  );
}

/** Layout of the logged-in portal screens */
export default function PortalLayout(): ReactNode {
  return (
    <ProtectedRoute>
      <PortalShell />
    </ProtectedRoute>
  );
}
