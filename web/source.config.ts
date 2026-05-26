import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { remarkMdxMermaid } from 'fumadocs-core/mdx-plugins';
import {
  remarkCodeBlockTitles,
  remarkLegacyCodeGroups,
} from './src/lib/remark-code-blocks';

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    postprocess: {
      includeProcessedMarkdown: {
        mdxAsPlaceholder: ['DemoWidget', 'PackageManagerTabs', 'Mermaid'],
      },
    },
  },
});

export default defineConfig({
  mdxOptions: {
    remarkPlugins: (plugins) => [
      remarkLegacyCodeGroups,
      ...plugins,
      remarkCodeBlockTitles,
      remarkMdxMermaid,
    ],
  },
});
