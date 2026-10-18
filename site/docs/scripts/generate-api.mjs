// Writes one MDX page per operation of docs/api/openapi.yaml into content/docs/api, plus an index.
// Only operations main serves are kept: the routes registered with handle() in internal/api/api.go.
// Operation descriptions are contract notes for implementers, so pages show the summary only.
// The pages and the filtered contract are generated, so they are gitignored; dev and build run this first.
import { readFileSync, writeFileSync } from 'node:fs';
import { parse } from 'yaml';
import { generateFiles } from 'fumadocs-openapi';
import { createOpenAPI } from 'fumadocs-openapi/server';

const routes = readFileSync('../../internal/api/api.go', 'utf8');
const project = routes.match(/const project = "([^"]+)"/)[1];
const served = new Set(
  [...routes.matchAll(/handle\(r, http\.Method(\w+), (project\+)?"([^"]+)"/g)].map(
    ([, method, prefix, path]) => `${method.toLowerCase()} ${prefix ? project : ''}${path}`,
  ),
);
if (served.size === 0) throw new Error('no handle() routes found in internal/api/api.go');

const contract = parse(readFileSync('../../docs/api/openapi.yaml', 'utf8'));
for (const [path, item] of Object.entries(contract.paths)) {
  for (const method of Object.keys(item)) {
    if (!['get', 'post', 'put', 'patch', 'delete'].includes(method)) continue;
    if (served.has(`${method} ${path}`)) {
      delete item[method].description;
      delete item[method]['x-access'];
    } else delete item[method];
  }
  if (!Object.keys(item).some((k) => ['get', 'post', 'put', 'patch', 'delete'].includes(k))) delete contract.paths[path];
}
// Decision records are internal notes, so their references are dropped from field descriptions.
writeFileSync('./openapi.served.json', JSON.stringify(contract).replace(/,? per decision \d{4}| \(decision \d{4}\)/g, ''));

await generateFiles({
  input: createOpenAPI({ input: ['./openapi.served.json'] }),
  output: './content/docs/api',
  index: {
    url: (file) => `/api/${file.split('/').pop().replace(/\.mdx$/, '')}`,
    items: [{ path: 'index.mdx', title: 'API reference', description: 'Every call the server answers, for your own scripts.' }],
  },
  meta: true,
  beforeWrite(files) {
    const meta = files.find((f) => f.path.endsWith('meta.json'));
    if (meta)
      meta.content = JSON.stringify(
        {
          title: 'API reference',
          description: 'Every call the server answers, for your own scripts.',
          icon: 'Code',
          root: true,
          ...JSON.parse(meta.content),
        },
        null,
        2,
      );
  },
});
