import { llms, loader } from 'fumadocs-core/source';
import { docsRoute } from './shared';
import { defineDocs } from 'fumadocs-mdx/macro';
import { openapiPlugin } from 'fumadocs-openapi/server';
import { metaSchema, pageSchema } from 'fumadocs-core/source/schema';
import { createElement } from 'react';
import { Code, Phone, RocketLaunch, Stack, TreeStructure, UsersThree } from '@phosphor-icons/react/ssr';

// Phosphor, the console's icon set, for the `icon` names in meta.json.
const icons = { Code, Phone, RocketLaunch, Stack, TreeStructure, UsersThree };

const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: pageSchema,
    postprocess: {
      includeProcessedMarkdown: true,
    },
  },
  meta: {
    schema: metaSchema,
  },
});

// See https://fumadocs.dev/docs/headless/source-api for more info
export const source = loader({
  baseUrl: docsRoute,
  source: docs.toFumadocsSource(),
  plugins: [openapiPlugin()],
  icon(name) {
    if (name && name in icons) return createElement(icons[name as keyof typeof icons]);
  },
});

export const docsLlms = llms(source, {
  renderPage: async (page) => `# ${page.data.title} (${page.url})

${await page.data.getText('processed')}`,
});
