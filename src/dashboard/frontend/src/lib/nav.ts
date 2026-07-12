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
  {
    group: 'Monitor',
    items: [
      { to: '/agents', label: 'Agents', id: 'agents' },
      { to: '/costs', label: 'Costs', id: 'costs' },
      { to: '/latency', label: 'Latency', id: 'latency' },
      { to: '/calls', label: 'Calls', id: 'calls' },
      { to: '/diagnostics', label: 'Diagnostics', id: 'diagnostics' },
    ],
  },
  {
    group: 'Configure',
    items: [
      { to: '/projects', label: 'Projects', id: 'projects' },
      { to: '/rate-card', label: 'Rate card', id: 'ratecard' },
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
