import { useState } from 'react';
import { setToken } from '../lib/api';

export default function Login({ onAuthed }: { onAuthed: () => void }) {
  const [token, setTokenValue] = useState('');

  const save = () => {
    const trimmed = token.trim();
    if (!trimmed) return;
    setToken(trimmed);
    onAuthed();
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
          onChange={(e) => setTokenValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') save();
          }}
        />
        <div
          className="flex-row mt-lg"
          style={{ justifyContent: 'flex-end' }}
        >
          <button
            className="neo-btn neo-btn--primary"
            onClick={save}
            disabled={!token.trim()}
          >
            Sign in
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
