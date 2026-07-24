// Human-friendly time formatting with a user-selectable timezone.
//
// Detail views rendered timestamps as full locale strings
// (`new Date(x).toLocaleString()`), which is noisy: seconds, full year, the
// works. These helpers render hours:minutes (with a short date where the day
// matters) in a timezone the user picks in Settings. The preference is a
// frontend-only choice persisted in localStorage; "auto" resolves to the
// browser zone, which for a co-located dashboard is the machine hosting the
// gateway.

import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'vg.timezone';
export const AUTO = 'auto';

type TzPref = string; // 'auto' or an IANA zone id

let current: TzPref = readStored();
const listeners = new Set<() => void>();

function readStored(): TzPref {
  try {
    return localStorage.getItem(STORAGE_KEY) || AUTO;
  } catch {
    return AUTO;
  }
}

/** The stored preference ('auto' or an IANA id). */
export function getTimeZonePref(): TzPref {
  return current;
}

/** Persist a new preference and notify subscribers. */
export function setTimeZonePref(tz: TzPref): void {
  current = tz || AUTO;
  try {
    localStorage.setItem(STORAGE_KEY, current);
  } catch {
    /* private mode: keep the in-memory value */
  }
  listeners.forEach((l) => l());
}

/** The IANA zone actually in effect ('auto' resolves to the browser/host zone). */
export function resolvedTimeZone(): string {
  if (current !== AUTO) return current;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Re-render on timezone changes; returns the current preference. */
export function useTimeZone(): TzPref {
  return useSyncExternalStore(subscribe, getTimeZonePref, getTimeZonePref);
}

const FALLBACK_ZONES = [
  'UTC',
  'America/Los_Angeles', 'America/Denver', 'America/Chicago',
  'America/New_York', 'America/Toronto', 'America/Sao_Paulo',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
  'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Asia/Shanghai',
  'Asia/Tokyo', 'Australia/Sydney',
];

/** Every IANA zone the runtime knows, for the picker (curated fallback). */
export function listTimeZones(): string[] {
  try {
    const all = (Intl as { supportedValuesOf?: (k: string) => string[] })
      .supportedValuesOf?.('timeZone');
    if (Array.isArray(all) && all.length) return all;
  } catch {
    /* fall through to the curated list */
  }
  return FALLBACK_ZONES;
}

// Numbers >= this are epoch milliseconds; smaller ones are epoch seconds.
// (ms for any date after 2001 exceeds 1e12; seconds stay well below it.)
const MS_THRESHOLD = 1e12;

function toDate(value: string | number | Date): Date | null {
  if (value instanceof Date) return value;
  if (typeof value === 'number') {
    return new Date(value < MS_THRESHOLD ? value * 1000 : value);
  }
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : new Date(t);
}

/** Hours:minutes in the effective timezone (e.g. "14:32"). */
export function formatTime(
  value: string | number | Date | null | undefined,
): string {
  if (value == null) return '—';
  const d = toDate(value);
  if (!d) return String(value);
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: resolvedTimeZone(),
  });
}

/** Short date + hours:minutes in the effective timezone (e.g. "Jul 24, 14:32"). */
export function formatDateTime(
  value: string | number | Date | null | undefined,
): string {
  if (value == null) return '—';
  const d = toDate(value);
  if (!d) return String(value);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: resolvedTimeZone(),
  });
}
