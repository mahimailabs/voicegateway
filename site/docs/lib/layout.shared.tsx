import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { DiscordLogo, LinkedinLogo } from '@phosphor-icons/react/ssr';
import { appName, gitConfig } from './shared';

// The console's wordmark (dashboard/src/assets/brand), one file per theme.
function Wordmark() {
  return (
    <>
      <img src="/brand/wordmark.svg" alt={appName} width={168} height={28} className="dark:hidden" />
      <img src="/brand/wordmark-dark.svg" alt={appName} width={168} height={28} className="hidden dark:block" />
    </>
  );
}

export function baseOptions(): BaseLayoutProps {
  return {
    nav: { title: <Wordmark /> },
    links: [
      {
        type: 'icon',
        label: 'Discord',
        text: 'Discord',
        icon: <DiscordLogo weight="fill" />,
        url: 'https://discord.gg/ysFaF4uSB',
        external: true,
      },
      {
        type: 'icon',
        label: 'LinkedIn',
        text: 'LinkedIn',
        icon: <LinkedinLogo weight="fill" />,
        url: 'https://www.linkedin.com/company/voicegatewaydev',
        external: true,
      },
    ],
    githubUrl: `https://github.com/${gitConfig.user}/${gitConfig.repo}`,
  };
}
