# 0030: Marketing site in a private repository

Date: 2026-10-18. Status: accepted, requested by the maintainer. Supersedes 0025.

voicegateway.dev is becoming a marketing site: campaigns, terms and privacy pages for Stripe, and advertising tracking. That work changes often, is not part of the product, and should not live in the open-source repository.

Move the landing page (`site/landing`) and its waitlist API Worker (`site/web`) to the private `mahimairaja/voicegateway-web` repository: the Next.js site at its root and the Worker under `worker/`. The Cloudflare Pages project `voicegateway-web` builds that repository and takes the voicegateway.dev domain; the `voicegateway-landing` project, which built `site/landing` from this repository, keeps running without the domain until the move is confirmed, so a rollback is one domain switch. The Worker, its D1 database, its stats secret and the shared PostHog setup are unchanged.

This repository keeps what the product needs: the documentation (`site/docs`), its palette (`site/theme.css`), and `install.sh` and `collector.sh`. The private site downloads those two scripts from this repository's main branch on every build, so voicegateway.dev/*.sh still serve exactly what is released here.
