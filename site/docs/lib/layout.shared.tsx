import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
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
    githubUrl: `https://github.com/${gitConfig.user}/${gitConfig.repo}`,
  };
}
