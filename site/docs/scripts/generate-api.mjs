// Writes one MDX page per operation of docs/api/openapi.yaml into content/docs/api, plus an index.
// The pages are generated, so they are gitignored; dev and build run this first.
import { generateFiles } from 'fumadocs-openapi';
import { createOpenAPI } from 'fumadocs-openapi/server';

await generateFiles({
  input: createOpenAPI({ input: ['../../docs/api/openapi.yaml'] }),
  output: './content/docs/api',
  includeDescription: true,
  index: {
    url: (file) => `/docs/api/${file.split('/').pop().replace(/\.mdx$/, '')}`,
    items: [{ path: 'index.mdx', title: 'API reference', description: 'Every call the console makes, for your own scripts.' }],
  },
  meta: true,
  beforeWrite(files) {
    const meta = files.find((f) => f.path.endsWith('meta.json'));
    if (meta) meta.content = JSON.stringify(
          {
            title: 'API reference',
            description: 'Every call the console makes, for your own scripts.',
            icon: 'Code',
            root: true,
            ...JSON.parse(meta.content),
          },
          null,
          2,
        );
  },
});
