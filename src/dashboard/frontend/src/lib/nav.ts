export interface NavLeaf {
  to: string;
  label: string;
  id: string;
}
export interface NavGroup {
  group: string;
  items: NavLeaf[];
}
export type NavEntry = NavLeaf | NavGroup;

export const NAV: NavEntry[] = [
  { to: '/', label: 'Overview', id: 'overview' },
  { to: '/sessions', label: 'Sessions', id: 'sessions' },
  { to: '/logs', label: 'Logs', id: 'logs' },
  {
    group: 'Monitor',
    items: [
      { to: '/costs', label: 'Costs', id: 'costs' },
      { to: '/latency', label: 'Latency', id: 'latency' },
      { to: '/metrics', label: 'Metrics', id: 'metrics' },
    ],
  },
  {
    group: 'Configure',
    items: [
      { to: '/agents', label: 'Agents', id: 'agents' },
      { to: '/providers', label: 'Providers', id: 'providers' },
      { to: '/models', label: 'Models', id: 'models' },
      { to: '/routing', label: 'Routing', id: 'routing' },
      { to: '/guardrails', label: 'Guardrails', id: 'guardrails' },
      { to: '/projects', label: 'Projects', id: 'projects' },
    ],
  },
];

/** Flat, searchable command list for the Cmd+K palette (includes footer routes). */
export const NAV_FLAT: { to: string; label: string; id: string; group: string }[] = [
  ...NAV.flatMap((e) =>
    'group' in e
      ? e.items.map((it) => ({ ...it, group: e.group }))
      : [{ ...e, group: 'Go to' }],
  ),
  { to: '/settings', label: 'Settings', id: 'settings', group: 'Account' },
  { to: '/api-keys', label: 'API keys', id: 'apikeys', group: 'Account' },
];
