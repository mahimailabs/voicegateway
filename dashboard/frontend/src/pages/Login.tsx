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
      }}
    >
      <div className="neo-card" style={{ maxWidth: 420, width: '100%' }}>
        <h3>VoiceGateway</h3>
        <p className="label mt-sm">
          This gateway requires an API token. Paste yours below to continue.
        </p>
        <label className="label mt-md">API token</label>
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
            className="label mt-sm"
            style={{ color: 'var(--accent-pink, #e11)' }}
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
            onClick={save}
            disabled={busy || !token.trim()}
          >
            {busy ? 'Checking…' : 'Sign in'}
          </button>
        </div>
        <p className="label mt-md" style={{ opacity: 0.7 }}>
          The token is stored in your browser (localStorage). It's never sent
          to third parties.
        </p>
      </div>
    </div>
  );
}
