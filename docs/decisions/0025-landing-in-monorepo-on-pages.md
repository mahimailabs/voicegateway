# 0025: Landing page in the main repository on Cloudflare Pages

Date: 2026-10-15. Status: superseded by 0030 (the marketing site moved to the private `voicegateway-web` repository).

The landing page belongs in `mahimailabs/voicegateway`, alongside the product and documentation. Import the current landing source into `site/landing`; the separate `voicegateway-web` repository is superseded. Cloudflare Pages builds this subdirectory with Node 24, pnpm 9 and a Next.js static export.

Keep the existing `voicegateway-web` API Worker in `site/web`, with its existing D1 database and stats secret. Pages forwards the waitlist and stats routes through a private `VOICEGATEWAY_API` service binding. The apex domain belongs to Pages, so the API Worker declares no custom-domain routes.

Preserve the production-only shared PostHog integration (`site=voicegateway`) and the mahimai-admin metrics page. This move changes source ownership and deployment configuration, not waitlist storage or analytics ownership. CI validates the landing app through `make landing-check`.
