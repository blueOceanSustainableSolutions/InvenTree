import { ActionIcon, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Frame of a portal screen: optional back button, title, badges, subtitle and
 * action buttons (they wrap onto new lines on phones), then the content.
 */
export function PortalPage({
  title,
  subtitle,
  badges,
  actions,
  back,
  children
}: Readonly<{
  title: ReactNode;
  subtitle?: ReactNode;
  badges?: ReactNode[];
  actions?: ReactNode[];
  back?: string;
  children?: ReactNode;
}>): ReactNode {
  const navigate = useNavigate();

  const visibleActions = (actions ?? []).filter(Boolean);

  return (
    <Stack gap='sm'>
      <Paper withBorder p='sm'>
        <Stack gap='xs'>
          <Group gap='xs' wrap='nowrap' align='flex-start'>
            {back && (
              <ActionIcon
                variant='subtle'
                size='lg'
                aria-label='portal-back'
                onClick={() => navigate(back)}
              >
                <IconArrowLeft />
              </ActionIcon>
            )}
            <Stack gap={2} style={{ minWidth: 0 }}>
              <Title order={3} style={{ overflowWrap: 'anywhere' }}>
                {title}
              </Title>
              {subtitle && (
                <Text size='sm' c='dimmed'>
                  {subtitle}
                </Text>
              )}
            </Stack>
          </Group>
          {badges && badges.filter(Boolean).length > 0 && (
            <Group gap='xs'>{badges}</Group>
          )}
          {visibleActions.length > 0 && (
            <Group gap='xs'>{visibleActions}</Group>
          )}
        </Stack>
      </Paper>
      {children}
    </Stack>
  );
}
