# voicegateway.dev

The production landing page lives beside this Worker in `site/landing`, in this repository, on Cloudflare Pages. This Astro Worker remains the API owner for `/api/waitlist` and `/api/stats`, using the existing `voicegateway-waitlist` D1 database. Pages calls it through a private `VOICEGATEWAY_API` service binding. The old static site is retained here, but the apex custom domain belongs to Pages. Do not reattach `voicegateway.dev` to this Worker.

```bash
npm ci
npm run dev       # http://localhost:4321
npm run build     # dist/client (static) + dist/server (Worker)
npm run deploy    # build, then deploy the API Worker (no custom domain)
```

- Palette: `../theme.css`, shared with `site/docs`. Page styles live next to each page.
- `collector.sh` and `install.sh` are copied from the repo root on every build, so `voicegateway.dev/*.sh` can never drift from what the repo ships. Don't commit copies.
- `public/demo` is a frozen build of the retired Python console's demo mode. Its source is no longer in this repo, so it cannot be rebuilt here.
- Waitlist: signups go to the `voicegateway-waitlist` D1 database (bound in `wrangler.jsonc`). `/api/stats?key=STATS_KEY` shows them. Secrets and optional vars are listed in `wrangler.jsonc`; set them with `npx wrangler secret put <NAME>`.
- Analytics: PostHog, only when `PUBLIC_POSTHOG_KEY` is set at build time.
- Types: `npm run check` generates `worker-configuration.d.ts` with `wrangler types`, then runs `astro check`. The secrets are typed in `src/env.d.ts`.

Voice: short declarative sentences, sentence-case headings, no em dashes (CI fails on one). The example call on the home page is priced with voice-prices; re-price it if you change the models.

## Hosting cutover, 2026-10-15

Pages project: `voicegateway-web`. Apex DNS: proxied CNAME to `voicegateway-web.pages.dev`. Existing D1 ID: `087d0e19-3e37-49a2-8750-ce52072a4f3c`. No data or secret migration was needed. The Pages frontend shares mahimai.ca PostHog with `site=voicegateway`; `mahimai-admin` reports its traffic, clicks and waitlist confirmations alongside the authenticated D1 totals.

Production verification: public site and demo deep links return 200, stats without a key returns 401, invalid waitlist input returns 400. A read-only D1 count confirmed database access with no writes. To roll back hosting, remove the Pages custom domain/CNAME and restore the Worker custom-domain mapping; keep the Worker and D1 intact throughout.
