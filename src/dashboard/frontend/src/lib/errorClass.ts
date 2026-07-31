// The failure taxonomy the Diagnostics error bars are grouped by.
//
// Every class here is decided by substring-matching an error string that a check
// (or a run) actually reported. Nothing is inferred: an error that matches
// nothing stays `other` rather than being guessed into a bucket, because a wrong
// cause is worse than an unclassified one when someone is debugging a deployment.
//
// Kept in `lib/` and not in the tab so the taxonomy has exactly one definition:
// the bars, and anything that later counts the same failures, cannot drift apart.

export type ErrorClass =
  | 'timeout'
  | 'no_worker'
  | 'no_reply'
  | 'auth'
  | 'config'
  | 'connect'
  | 'other';

export const ERROR_CLASS_LABEL: Record<ErrorClass, string> = {
  timeout: 'Timed out',
  no_worker: 'No worker joined',
  no_reply: 'No reply from agent',
  auth: 'Auth rejected',
  config: 'Not configured',
  connect: 'Could not connect',
  other: 'Other',
};

export const ERROR_CLASS_COLOR: Record<ErrorClass, string> = {
  timeout: 'var(--vg-amber)',
  no_worker: 'var(--vg-red)',
  no_reply: 'var(--vg-red)',
  auth: 'var(--vg-red)',
  config: 'var(--vg-muted)',
  connect: 'var(--vg-amber)',
  other: 'var(--vg-muted-2)',
};

/** Display order: the classes an operator can act on first, `other` last. */
export const ERROR_CLASS_ORDER: ErrorClass[] = [
  'timeout',
  'no_worker',
  'no_reply',
  'auth',
  'connect',
  'config',
  'other',
];

/**
 * Bucket one error string. Substring matching only: no cause is invented.
 *
 * `no_reply` is deliberately NOT reachable from here. It is not a reported error
 * at all - it is a latency check that succeeded while measuring zero trials, so
 * only the caller that can see the trial count may assign it.
 */
export function classifyError(message: string): ErrorClass {
  const m = message.toLowerCase();
  if (m.includes('timed out') || m.includes('timeout')) return 'timeout';
  if (m.includes('no worker joined') || m.includes('worker joined')) return 'no_worker';
  if (
    m.includes('401') ||
    m.includes('403') ||
    m.includes('unauthorized') ||
    m.includes('forbidden') ||
    m.includes('invalid api key')
  ) {
    return 'auth';
  }
  if (m.includes('not configured') || m.includes('missing livekit credentials')) return 'config';
  if (
    m.includes('connect') ||
    m.includes('connection') ||
    m.includes('refused') ||
    m.includes('unreachable') ||
    m.includes('dns') ||
    m.includes('handshake') ||
    m.includes('ssl') ||
    m.includes('tls')
  ) {
    return 'connect';
  }
  return 'other';
}

/** One failure after classification: what class, where it came from, what it said. */
export interface ClassifiedError {
  /** The check name that reported it, or `run` for a whole-run error. */
  where: string;
  message: string;
  klass: ErrorClass;
}
