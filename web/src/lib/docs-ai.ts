import {
  renderPlaceholder,
  type PlaceholderData,
} from 'fumadocs-core/mdx-plugins/remark-llms.runtime';
import { source } from './source';

const SITE_URL = 'https://voicegateway.mahimai.ca';
const SDK_REPO_URL = 'https://github.com/mahimailabs/voicegateway/blob/main';
const WEB_REPO_URL = 'https://github.com/mahimailabs/voicegateway-web/blob/main';

type DocsPage = NonNullable<ReturnType<typeof source.getPage>>;

export function getDocsGithubUrl(page: DocsPage): string {
  if (page.path === 'changelog.mdx') {
    return `${SDK_REPO_URL}/CHANGELOG.md`;
  }

  if (page.path === 'get-started.mdx') {
    return `${SDK_REPO_URL}/docs/get-started.md`;
  }

  if (page.path.endsWith('.md')) {
    return `${SDK_REPO_URL}/docs/${page.path}`;
  }

  return `${WEB_REPO_URL}/content/docs/${page.path}`;
}

export function getCanonicalUrl(pathname: string): string {
  return new URL(pathname, SITE_URL).toString();
}

export function uniqueDocsPages(): DocsPage[] {
  const seen = new Set<string>();
  const pages: DocsPage[] = [];

  for (const page of source.getPages()) {
    if (seen.has(page.url)) continue;

    seen.add(page.url);
    pages.push(page);
  }

  return pages.sort((a, b) => {
    if (a.url === '/docs') return -1;
    if (b.url === '/docs') return 1;

    return a.url.localeCompare(b.url);
  });
}

export async function getDocsPageMarkdown(page: DocsPage): Promise<string> {
  return renderPlaceholder(await page.data.getText('processed'), {
    DemoWidget: renderDemoWidgetPlaceholder,
    PackageManagerTabs: renderPackageManagerTabsPlaceholder,
  });
}

function renderDemoWidgetPlaceholder() {
  return [
    '> Interactive demo widget',
    '>',
    '> Open this page in the browser to try the local mocked VoiceGateway demo.',
  ].join('\n');
}

function renderPackageManagerTabsPlaceholder({ attributes }: PlaceholderData) {
  const pkg = typeof attributes.pkg === 'string' ? attributes.pkg : 'voicegateway';

  return [
    '```bash',
    `uv add ${pkg}`,
    `pip install ${pkg}`,
    `poetry add ${pkg}`,
    '```',
  ].join('\n');
}
