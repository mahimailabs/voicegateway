import { useState } from 'react';
import { setToken } from '../lib/api';

export default function Login({ onAuthed }: { onAuthed: () => void }) {
  const [token, setTokenValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const trimmed = token.trim();
    if (!trimmed) return;

    setBusy(true);
    setError(null);

    // Probe the API with the candidate token before committing it to
    // localStorage. /v1/status is cheap and doesn't return anything
    // sensitive; any status other than 200 means the token is no good.
    try {
      const res = await fetch('/v1/status', {
        headers: { Authorization: `Bearer ${trimmed}` },
      });
      if (res.status === 401 || res.status === 403) {
        setError('Token rejected by the gateway. Double-check and try again.');
        return;
      }
      if (!res.ok) {
        setError(`Unexpected response (${res.status}). Try again in a moment.`);
        return;
      }
      setToken(trimmed);
      onAuthed();
    } catch (e) {
      setError((e as Error).message || 'Network error while validating token.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="login-gate"
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(160deg, var(--vg-teal-tint-2) 0%, var(--vg-paper) 60%)',
        padding: '24px',
      }}
    >
      <div
        className="neo-card"
        style={{
          maxWidth: 420,
          width: '100%',
          padding: '32px',
          boxShadow: 'var(--vg-shadow-lg)',
        }}
      >
        {/* Brand mark + wordmark */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
          <span
            aria-hidden="true"
            style={{
              width: 32, height: 32, borderRadius: 10, flexShrink: 0,
              background: 'linear-gradient(135deg, var(--vg-teal-bright), var(--vg-teal-deep))',
              boxShadow: '0 2px 8px -2px rgba(21,120,138,0.5), inset 0 1px 0 rgba(255,255,255,0.25)',
              display: 'grid', placeItems: 'center',
            }}
          />
          <span
            style={{
              fontWeight: 800,
              fontSize: 18,
              letterSpacing: '-0.4px',
              color: 'var(--vg-ink)',
            }}
          >
            VoiceGateway
          </span>
        </div>

        <h3 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Sign in</h3>
        <p className="label mt-sm" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400, fontSize: 13, color: 'var(--vg-muted)' }}>
          This gateway requires an API token. Paste yours below to continue.
        </p>

        <label className="label mt-md" style={{ display: 'block', marginBottom: 6 }}>API token</label>
        <input
          className="neo-input"
          type="password"
          autoFocus
          placeholder="Bearer token"
          value={token}
          onChange={(e) => {
            setTokenValue(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') save();
          }}
        />
        {error && (
          <div
            className="mt-sm"
            style={{ color: 'var(--vg-red)', fontSize: 13, fontWeight: 500 }}
          >
            {error}
          </div>
        )}
        <div
          className="flex-row mt-lg"
          style={{ justifyContent: 'flex-end' }}
        >
          <button
            className="neo-btn neo-btn--primary"
            style={{ width: '100%', justifyContent: 'center', padding: '11px 16px' }}
            onClick={save}
            disabled={busy || !token.trim()}
          >
            {busy ? 'Checking…' : 'Sign in'}
          </button>
        </div>
        <p className="label mt-md" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400, fontSize: 12, color: 'var(--vg-muted-2)', opacity: 0.85 }}>
          The token is stored in your browser (localStorage) and is never sent
          to third parties.
        </p>
      </div>
    </div>
  );
}
