import { useEffect, useMemo, useState } from 'react';
import PageHeader from '../components/PageHeader';
import {
  createApiKey,
  fetchApiKeys,
  revokeApiKey,
} from '../lib/api';
import type { CreatedApiKey, ApiKey } from '../lib/types';

const STALE_DAYS_DEFAULT = 90;

function daysSince(iso: string): number {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 0;
  return Math.floor((Date.now() - then) / 86400000);
}

function isStale(k: ApiKey, threshold: number): boolean {
  if (k.revoked_at !== null) return false;
  const last = k.last_used_at ?? k.issued_at;
  return daysSince(last) >= threshold;
}

function formatRelative(iso: string | null): string {
  if (iso === null) return 'never';
  const d = daysSince(iso);
  if (d === 0) return 'today';
  if (d === 1) return 'yesterday';
  if (d < 30) return `${d}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

export default function ApiKeys() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [includeRevoked, setIncludeRevoked] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showKey, setShowKey] = useState<CreatedApiKey | null>(null);

  const refresh = () => {
    fetchApiKeys({ includeRevoked })
      .then((d) => setKeys(d.keys))
      .catch(() => setKeys([]));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeRevoked]);

  const staleCount = useMemo(
    () => keys.filter((k) => isStale(k, STALE_DAYS_DEFAULT)).length,
    [keys],
  );
  const activeCount = useMemo(
    () => keys.filter((k) => k.revoked_at === null).length,
    [keys],
  );

  return (
    <div>
      <PageHeader
        title="API Keys"
        subtitle={`${activeCount} active · ${staleCount} stale · ${keys.length} total`}
        accent="orange"
        actions={
          <button
            className="neo-btn neo-btn--primary"
            onClick={() => setShowCreate(true)}
          >
            + Issue Key
          </button>
        }
      />

      <div className="flex-row mt-md" style={{ justifyContent: 'flex-end' }}>
        <label className="label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <input
            type="checkbox"
            checked={includeRevoked}
            onChange={(e) => setIncludeRevoked(e.target.checked)}
          />
          Show revoked
        </label>
      </div>

      <div className="neo-card mt-md">
        {keys.length === 0 ? (
          <div className="empty-state">No API keys issued yet.</div>
        ) : (
          <table className="neo-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Tenant</th>
                <th>Issued</th>
                <th>Last used</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const stale = isStale(k, STALE_DAYS_DEFAULT);
                const revoked = k.revoked_at !== null;
                return (
                  <tr key={k.id}>
                    <td>
                      <strong>{k.name}</strong>
                      {k.issued_by && (
                        <div className="label" style={{ fontSize: '0.75rem' }}>
                          by {k.issued_by}
                        </div>
                      )}
                    </td>
                    <td>
                      <code>{k.key_prefix}…</code>
                    </td>
                    <td>
                      {k.tenant_id ? (
                        <span className="neo-badge neo-badge--black">
                          {k.tenant_id}
                        </span>
                      ) : (
                        <span className="label" style={{ opacity: 0.6 }}>
                          unscoped
                        </span>
                      )}
                    </td>
                    <td className="label">{formatRelative(k.issued_at)}</td>
                    <td className="label">{formatRelative(k.last_used_at)}</td>
                    <td>
                      {revoked ? (
                        <span className="neo-badge neo-badge--black">revoked</span>
                      ) : stale ? (
                        <span className="neo-badge" style={{ background: '#FFD166' }}>
                          stale
                        </span>
                      ) : (
                        <span className="neo-badge" style={{ background: 'var(--accent-green)' }}>
                          active
                        </span>
                      )}
                    </td>
                    <td>
                      {!revoked && <RevokeButton id={k.id} onDone={refresh} />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {showCreate && (
        <IssueKeyModal
          onCancel={() => setShowCreate(false)}
          onIssued={(resp) => {
            setShowCreate(false);
            setShowKey(resp);
            refresh();
          }}
        />
      )}

      {showKey && (
        <ShowKeyOnceModal
          response={showKey}
          onClose={() => setShowKey(null)}
        />
      )}
    </div>
  );
}

function RevokeButton({ id, onDone }: { id: number; onDone: () => void }) {
  const [busy, setBusy] = useState(false);

  const revoke = async () => {
    if (!confirm('Revoke this key? It cannot be undone.')) return;
    setBusy(true);
    try {
      await revokeApiKey(id);
      onDone();
    } catch (e) {
      alert((e as Error).message || 'Failed to revoke key');
    } finally {
      setBusy(false);
    }
  };

  return (
    <button className="neo-btn" onClick={revoke} disabled={busy}>
      {busy ? '…' : 'Revoke'}
    </button>
  );
}

function IssueKeyModal({
  onCancel,
  onIssued,
}: {
  onCancel: () => void;
  onIssued: (resp: CreatedApiKey) => void;
}) {
  const [name, setName] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [issuedBy, setIssuedBy] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const resp = await createApiKey({
        name,
        tenant_id: tenantId.trim() || null,
        issued_by: issuedBy.trim() || null,
      });
      onIssued(resp);
    } catch (e) {
      alert((e as Error).message || 'Failed to issue key');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onCancel}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Issue API Key</h3>
        <label className="label">Name</label>
        <input
          className="neo-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="prod-bot"
          autoFocus
        />
        <label className="label mt-md">Tenant scope (optional)</label>
        <input
          className="neo-input"
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
          placeholder="acme"
        />
        <div className="label mt-sm" style={{ fontSize: '0.75rem', opacity: 0.7 }}>
          Scoped keys auto-tag every session with this tenant. Conflicting
          body tenants return 403.
        </div>
        <label className="label mt-md">Issued by (optional)</label>
        <input
          className="neo-input"
          value={issuedBy}
          onChange={(e) => setIssuedBy(e.target.value)}
          placeholder="ops@example.com"
        />
        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onCancel}>Cancel</button>
          <button
            className="neo-btn neo-btn--primary"
            onClick={save}
            disabled={saving || !name.trim()}
          >
            {saving ? 'Issuing…' : 'Issue Key'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ShowKeyOnceModal({
  response,
  onClose,
}: {
  response: CreatedApiKey;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(response.plaintext).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div
        className="neo-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: '32rem' }}
      >
        <h3>Save this key now</h3>
        <p className="label mt-sm">
          The full key appears <strong>only once</strong>. Copy it into your
          secret store before closing this modal: the dashboard cannot show it
          again.
        </p>
        <div
          className="neo-card mt-md"
          style={{ background: '#FFF9C2', padding: '1rem' }}
        >
          <code style={{ wordBreak: 'break-all', fontSize: '0.95rem' }}>
            {response.plaintext}
          </code>
        </div>
        <div className="flex-row mt-md" style={{ justifyContent: 'space-between' }}>
          <span className="label">
            Name: <strong>{response.row.name}</strong> · Tenant:{' '}
            <strong>{response.row.tenant_id ?? 'unscoped'}</strong>
          </span>
          <div className="flex-row">
            <button className="neo-btn" onClick={copy}>
              {copied ? 'Copied!' : 'Copy'}
            </button>
            <button className="neo-btn neo-btn--primary" onClick={onClose}>
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
