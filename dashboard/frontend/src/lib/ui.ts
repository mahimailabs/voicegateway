export type Accent = 'yellow' | 'blue' | 'green' | 'pink' | 'orange';

export const ACCENT_COLORS: Record<Accent, string> = {
  yellow: '#FFD166',
  blue: '#118AB2',
  green: '#8AC926',
  pink: '#FF006E',
  orange: '#FB8500',
};

export function formatCost(value: number | undefined | null, decimals = 2): string {
  if (value == null) return '$0.00';
  return `$${value.toFixed(decimals)}`;
}

export function formatMs(value: number | undefined | null): string {
  if (value == null || value === 0) return '-';
  return `${Math.round(value)}ms`;
}

export function latencyBadgeClass(ms: number | undefined | null): string {
  if (ms == null || ms === 0) return 'neo-badge--black';
  if (ms < 200) return 'neo-badge--online';
  if (ms < 500) return 'neo-badge--yellow';
  return 'neo-badge--offline';
}

export function statusBadgeClass(status: string | undefined): string {
  if (!status) return 'neo-badge--black';
  if (status === 'success') return 'neo-badge--online';
  if (status === 'fallback') return 'neo-badge--yellow';
  return 'neo-badge--offline';
}
