# VoiceGateway docs

The site at <https://docs.voicegateway.dev>: a [Fumadocs](https://fumadocs.dev) app, statically exported and served from Cloudflare Workers static assets.

```bash
npm ci
npm run dev     # http://localhost:3000
npm run build   # static export in ./out
npx wrangler deploy   # serves ./out at docs.voicegateway.dev (wrangler.jsonc)
```

- Pages: MDX in `content/docs/`, ordered by `content/docs/meta.json`. Every page needs `title` and `description` frontmatter.
- Site name and GitHub links: `lib/shared.ts`. Look: the console's "Clean Room" palette (DESIGN.md), mapped onto Fumadocs' variables in `app/global.css`; the wordmark and favicon are copies of the console's.
- Cloudflare: the project root is `site/docs`, but the build reads `../../docs/api/openapi.yaml`, so deploy from a full checkout.
- Pictures: `public/diagrams/` holds Reladraw renders, a light and a dark SVG per diagram (only the images are kept in git); `public/screens/` holds console screenshots. Show one with `<Picture src="/diagrams/name" alt="..." />`.
- API reference: `scripts/generate-api.mjs` writes `content/docs/api/` from `docs/api/openapi.yaml` before every dev and build run. It is gitignored.
- Don't edit `.source/`; Fumadocs MDX generates it.

Keep it small. Short declarative sentences, sentence-case headings, no em dashes: CI fails on one.
