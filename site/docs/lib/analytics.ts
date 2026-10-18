import type { PostHog } from 'posthog-js';

// Same PostHog project and setup as voicegateway.dev (site/landing/src/lib/analytics.ts):
// production host only, in-memory ids, no cookies, no autocapture, no recordings. Every
// event carries site: 'voicegateway-docs' so the admin dashboard can tell docs from the
// landing page ('voicegateway') and from mahimai.ca (untagged).
export const SITE = 'voicegateway-docs';
const KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || 'phc_s2dddKHsS4kEnJ925VzsKYQh8hYGdohzEadc8CUhkoxV';
const HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST || 'https://m.mahimai.ca';
let pending: Promise<PostHog | null> | undefined;
let lastPage: string | undefined;

export function initializeAnalytics(): Promise<PostHog | null> {
  if (typeof window === 'undefined' || window.location.hostname !== 'docs.voicegateway.dev' || !KEY) return Promise.resolve(null);
  pending ??= import('posthog-js').then(({ default: posthog }) => {
    posthog.init(KEY, {
      api_host: HOST,
      ui_host: 'https://us.posthog.com',
      defaults: '2026-05-30',
      persistence: 'memory',
      person_profiles: 'identified_only',
      capture_pageview: false,
      autocapture: false,
      disable_surveys: true,
      disable_session_recording: true,
      before_send: event => {
        if (event) event.properties.site = SITE;
        return event;
      },
    });
    posthog.register({ site: SITE });
    return posthog;
  }).catch(() => null);
  return pending;
}

export async function track(event: string, properties: Record<string, string | number> = {}): Promise<void> {
  try { (await initializeAnalytics())?.capture(event, { ...properties, site: SITE }); } catch { /* Analytics never blocks the docs. */ }
}

export async function trackPageview(path: string): Promise<void> {
  const client = await initializeAnalytics();
  if (!client || lastPage === path) return;
  lastPage = path;
  client.capture('$pageview', { $current_url: window.location.origin + path, $pathname: path, site: SITE });
}

const AI_HOSTS = new Set(['chatgpt.com', 'claude.ai', 'cursor.com', 'scira.ai']);

/** Names the event for a click on a link that leaves the docs, or null for links within them. */
export function linkEvent(href: string, origin: string): { name: string; destination: string } | null {
  let target: URL;
  try { target = new URL(href, origin); } catch { return null; }
  if (target.origin === origin || !target.protocol.startsWith('http')) return null;
  const host = target.hostname.replace(/^www\./, '');
  const name = host === 'github.com' ? 'github_click'
    : host === 'dash.voicegateway.dev' ? 'console_click'
    : host === 'voicegateway.dev' ? 'landing_click'
    : AI_HOSTS.has(host) ? 'ask_ai_click'
    : 'outbound_click';
  return { name, destination: target.origin + target.pathname };
}
