import { useEffect } from 'react';
import { fetchJson } from './api';

interface BrandingPayload {
  logo_url?: string | null;
  accent_color?: string | null;
  product_name?: string | null;
}

interface ProjectBrandingResponse {
  project_id: string;
  branding: BrandingPayload | null;
}

const DEFAULT_PRODUCT_NAME = 'VoiceGateway';
const _BRAND_VARS = ['--brand-accent', '--brand-product-name', '--brand-logo-url'] as const;

/**
 * Read the active project's branding once and apply it as CSS variables
 * on ``document.documentElement``. Implements REQ-VG-ROUTE-004 AC-2.
 *
 * Read-once-per-mount per OQ5: this function is meant to run on
 * layout mount (via :func:`useBranding`). Operator brand changes
 * surface on the next page load, not live.
 *
 * Behavior:
 * - ``accent_color`` -> sets ``--brand-accent`` on ``document.documentElement``
 *   and the favicon link's color attribute when present.
 * - ``product_name`` -> sets ``--brand-product-name`` AND
 *   ``document.title`` (prefixed with the active page label upstream).
 * - ``logo_url`` -> sets ``--brand-logo-url`` (CSS ``url(...)``) for
 *   ``AppShell`` to consume as a ``background-image`` or
 *   ``<img src>``; also updates the favicon ``<link rel="icon">``.
 * - When the project is unknown OR has no branding set, clears the
 *   three CSS variables so the dashboard renders the VoiceGateway
 *   default (REQ-VG-ROUTE-004 AC-3).
 */
export async function applyBrandingForProject(
  projectId: string | null,
): Promise<BrandingPayload | null> {
  if (!projectId) {
    clearBranding();
    return null;
  }
  let branding: BrandingPayload | null = null;
  try {
    const resp = await fetchJson<ProjectBrandingResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/branding`,
    );
    branding = resp.branding;
  } catch {
    // 404 unknown project, or 503 storage off: fall through to defaults.
    clearBranding();
    return null;
  }
  applyBranding(branding);
  return branding;
}

/** Apply a branding payload to the document root. Used directly by tests. */
export function applyBranding(branding: BrandingPayload | null): void {
  const root = document.documentElement;
  if (!branding) {
    clearBranding();
    return;
  }
  if (branding.accent_color) {
    root.style.setProperty('--brand-accent', branding.accent_color);
  } else {
    root.style.removeProperty('--brand-accent');
  }
  if (branding.product_name) {
    root.style.setProperty(
      '--brand-product-name',
      `"${branding.product_name.replace(/"/g, '\\"')}"`,
    );
    document.title = branding.product_name;
  } else {
    root.style.removeProperty('--brand-product-name');
    document.title = DEFAULT_PRODUCT_NAME;
  }
  if (branding.logo_url) {
    root.style.setProperty('--brand-logo-url', `url("${branding.logo_url}")`);
    _setFavicon(branding.logo_url);
  } else {
    root.style.removeProperty('--brand-logo-url');
    _setFavicon(null);
  }
}

/** Reset all three CSS variables back to defaults (AC-3 / AC-4 fallback). */
export function clearBranding(): void {
  const root = document.documentElement;
  for (const name of _BRAND_VARS) {
    root.style.removeProperty(name);
  }
  document.title = DEFAULT_PRODUCT_NAME;
  _setFavicon(null);
}

function _setFavicon(href: string | null): void {
  const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
  if (!link) return;
  if (href) {
    link.href = href;
  } else {
    // Reset to the bundled default if vite has set one via index.html.
    const original = link.getAttribute('data-default-href');
    if (original) link.href = original;
  }
}

/**
 * React hook: applies branding for the URL's ``?project=<id>`` (or
 * the supplied default) once on mount. Returns the resolved branding
 * dict or null so layouts can do conditional UI (e.g. show or hide
 * the default logo).
 *
 * Per OQ5, this hook does NOT subscribe to project changes. A user
 * who switches projects in the FilterBar sees the new brand on the
 * next page navigation (when the AppShell remounts) — matching
 * REQ-VG-ROUTE-004 AC-4 wording "on the next page load".
 */
export function useBranding(defaultProjectId?: string): void {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectId = params.get('project') ?? defaultProjectId ?? null;
    void applyBrandingForProject(projectId);
    // Intentionally NO dependency on location.search — read-once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
