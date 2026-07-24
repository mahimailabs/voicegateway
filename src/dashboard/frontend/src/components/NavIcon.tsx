// Compact line icons (Lucide-style, currentColor) keyed by nav id, so the
// sidebar stays legible in collapsed icon-only mode.

const PATHS: Record<string, JSX.Element> = {
  overview: <><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>,
  calls: <><path d="M3 5h18M3 12h18M3 19h18" /><circle cx="7" cy="5" r="0.5" /></>,
  costs: <><circle cx="12" cy="12" r="9" /><path d="M12 7v10M9.5 9.5a2.5 2 0 0 1 5 0c0 1.4-5 1-5 2.8a2.5 2 0 0 0 5 0" /></>,
  latency: <><circle cx="12" cy="13" r="8" /><path d="M12 13V9M12 5V3M9 3h6" /></>,
  agents: <><rect x="5" y="8" width="14" height="11" rx="2" /><path d="M12 8V4M9 13h.01M15 13h.01M9 19v2M15 19v2" /></>,
  providers: <><path d="M12 3v6M8 9h8a3 3 0 0 1 3 3v1H5v-1a3 3 0 0 1 3-3ZM7 13v4a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-4" /></>,
  models: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M9 9h6v6H9zM4 9h0M20 9h0M4 15h0M20 15h0M9 4v0M15 4v0M9 20v0M15 20v0" /></>,
  routing: <><circle cx="6" cy="6" r="2" /><circle cx="6" cy="18" r="2" /><circle cx="18" cy="12" r="2" /><path d="M8 6h4a4 4 0 0 1 4 4v0M8 18h4a4 4 0 0 0 4-4v0" /></>,
  projects: <><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></>,
  docs: <><path d="M5 4h9l5 5v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1Z" /><path d="M14 4v5h5M9 13h6M9 17h6" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></>,
  apikeys: <><circle cx="8" cy="14" r="4" /><path d="M11 11l8-8M16 6l3 3M14 8l2 2" /></>,
  account: <><circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 4-6 8-6s8 2 8 6" /></>,
  diagnostics: <><path d="M12 13l4-3" /><path d="M4 18a8 8 0 1 1 16 0" /><circle cx="12" cy="13" r="1.2" /></>,
  server: <><rect x="4" y="4" width="16" height="7" rx="1.5" /><rect x="4" y="13" width="16" height="7" rx="1.5" /><path d="M7.5 7.5h.01M7.5 16.5h.01" /></>,
};

export default function NavIcon({ id, size = 17 }: { id: string; size?: number }) {
  const path = PATHS[id] ?? PATHS.overview;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {path}
    </svg>
  );
}
