// Rate card page: the billing rate card in effect (YAML seed + DB overrides)
// plus CRUD over the DB overrides. Mirrors the Providers page pattern:
// read via fetchJson, per-row delete, an Add modal that refreshes on close.

import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import { fetchJson } from '../lib/api';

interface EffectiveRule {
  modality: string;
  provider: string;
  model: string;
  tenant: string | null;
  plan: string | null;
  kind: string;
  markup: number | null;
  unit_price_usd: number | null;
  unit: string | null;
  rule: string;
}

interface DbRule extends EffectiveRule {
  rule_id: string;
  created_at: number;
  updated_at: number;
}

function scopeLabel(r: {
  tenant: string | null;
  plan: string | null;
  modality: string;
  provider: string;
  model: string;
}): string {
  const parts = [r.tenant, r.plan, r.modality, r.provider, r.model].filter(
    (x) => x && x !== '*',
  );
  return parts.length ? parts.join(' / ') : '(global)';
}

function valueLabel(r: {
  kind: string;
  markup: number | null;
  unit_price_usd: number | null;
  unit: string | null;
}): string {
  if (r.kind === 'fixed') {
    return `$${(r.unit_price_usd ?? 0).toLocaleString(undefined, {
      maximumFractionDigits: 6,
    })}/${r.unit}`;
  }
  return `${(r.markup ?? 1).toLocaleString(undefined, { maximumFractionDigits: 4 })}x`;
}

export default function RateCard() {
  const [defaultMarkup, setDefaultMarkup] = useState<number | null>(null);
  const [effective, setEffective] = useState<EffectiveRule[]>([]);
  const [dbRules, setDbRules] = useState<DbRule[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    setError(null);
    fetchJson<{ default_markup: number; rules: EffectiveRule[] }>(
      '/v1/billing/rate-card',
    )
      .then((d) => {
        setDefaultMarkup(d.default_markup);
        setEffective(d.rules);
      })
      .catch((e) => setError((e as Error).message || 'Failed to load rate card'));
    fetchJson<{ rules: DbRule[] }>('/v1/billing/rate-card/rules')
      .then((d) => setDbRules(d.rules))
      .catch(() => setDbRules([]));
  };

  useEffect(refresh, []);

  const handleDelete = async (rule: DbRule) => {
    if (!window.confirm(`Delete override for ${scopeLabel(rule)}?`)) return;
    try {
      await fetchJson(`/v1/billing/rate-card/rules/${encodeURIComponent(rule.rule_id)}`, {
        method: 'DELETE',
      });
      refresh();
    } catch (e) {
      setError((e as Error).message || 'Delete failed');
    }
  };

  return (
    <div>
      <PageHeader
        title="Rate card"
        subtitle={
          defaultMarkup === null
            ? 'Billing rate card'
            : `Default markup ${defaultMarkup}x · ${dbRules.length} override${
                dbRules.length === 1 ? '' : 's'
              }`
        }
        accent="orange"
        actions={
          <button className="neo-btn neo-btn--primary" onClick={() => setShowAdd(true)}>
            + Add override
          </button>
        }
      />

      {error && (
        <div
          className="vg-card mt-md"
          style={{ color: 'var(--vg-red, #c0392b)', fontWeight: 500 }}
        >
          {error}
        </div>
      )}

      <section className="vg-card mt-md">
        <h3 style={{ color: 'var(--vg-ink)', marginBottom: 4 }}>Effective rate card</h3>
        <p style={{ fontSize: 13, color: 'var(--vg-muted)', marginBottom: 16 }}>
          The rules in effect right now: the <code>rate_card:</code> seed in{' '}
          <code>voicegw.yaml</code> merged with the DB overrides below. A DB override
          wins a tie against the seed rule at the same scope.
        </p>
        {effective.length === 0 ? (
          <div className="empty-state">
            No rules. Every request bills at the default markup.
          </div>
        ) : (
          <table className="neo-table">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Kind</th>
                <th>Price</th>
                <th>Rule</th>
              </tr>
            </thead>
            <tbody>
              {effective.map((r, i) => (
                <tr key={i}>
                  <td>{scopeLabel(r)}</td>
                  <td>
                    <span
                      className={`neo-badge ${
                        r.kind === 'fixed' ? 'neo-badge--blue' : 'neo-badge--green'
                      }`}
                    >
                      {r.kind}
                    </span>
                  </td>
                  <td>{valueLabel(r)}</td>
                  <td className="mono" style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
                    {r.rule}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="vg-card mt-md">
        <h3 style={{ color: 'var(--vg-ink)', marginBottom: 4 }}>DB overrides</h3>
        <p style={{ fontSize: 13, color: 'var(--vg-muted)', marginBottom: 16 }}>
          Database-persisted rules you can edit here. One rule per scope. Changes take
          effect on the next config refresh.
        </p>
        {dbRules.length === 0 ? (
          <div className="empty-state">
            No overrides yet. Add one to change rates without editing YAML.
          </div>
        ) : (
          <table className="neo-table">
            <thead>
              <tr>
                <th>Scope</th>
                <th>Kind</th>
                <th>Price</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {dbRules.map((r) => (
                <tr key={r.rule_id}>
                  <td>{scopeLabel(r)}</td>
                  <td>
                    <span
                      className={`neo-badge ${
                        r.kind === 'fixed' ? 'neo-badge--blue' : 'neo-badge--green'
                      }`}
                    >
                      {r.kind}
                    </span>
                  </td>
                  <td>{valueLabel(r)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="neo-btn neo-btn--sm neo-btn--danger"
                      onClick={() => handleDelete(r)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {showAdd && (
        <AddOverrideModal
          onClose={() => {
            setShowAdd(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

const UNITS = [
  'minute',
  'second',
  'char',
  '1k_char',
  'token',
  '1k_token',
  '1m_token',
  'request',
];

function AddOverrideModal({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<'cost_plus' | 'fixed'>('cost_plus');
  const [modality, setModality] = useState('*');
  const [provider, setProvider] = useState('*');
  const [model, setModel] = useState('*');
  const [tenant, setTenant] = useState('');
  const [plan, setPlan] = useState('');
  const [markup, setMarkup] = useState('1.3');
  const [fixed, setFixed] = useState('0.006');
  const [unit, setUnit] = useState('minute');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    const body: Record<string, unknown> = {
      modality: modality.trim() || '*',
      provider: provider.trim() || '*',
      model: model.trim() || '*',
      tenant: tenant.trim() || null,
      plan: plan.trim() || null,
    };
    if (kind === 'cost_plus') {
      body.markup = parseFloat(markup);
    } else {
      body.fixed = parseFloat(fixed);
      body.unit = unit;
    }
    try {
      await fetchJson('/v1/billing/rate-card/rules', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Save failed');
      setSaving(false);
    }
  };

  const labelStyle = { display: 'block', marginBottom: 6 } as const;
  const fullWidth = { width: '100%' } as const;

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginBottom: 16, color: 'var(--vg-ink)' }}>Add rate override</h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label className="vg-card__label" style={labelStyle}>
              Rule type
            </label>
            <select
              className="neo-select"
              style={fullWidth}
              value={kind}
              onChange={(e) => setKind(e.target.value as 'cost_plus' | 'fixed')}
              disabled={saving}
            >
              <option value="cost_plus">Cost plus (markup on provider cost)</option>
              <option value="fixed">Fixed ($/unit)</option>
            </select>
          </div>

          <div className="flex-row" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="vg-card__label" style={labelStyle}>
                Modality
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                value={modality}
                onChange={(e) => setModality(e.target.value)}
                placeholder="stt, llm, tts, *"
                disabled={saving}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label className="vg-card__label" style={labelStyle}>
                Provider
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                placeholder="openai, deepgram, *"
                disabled={saving}
              />
            </div>
          </div>

          <div className="flex-row" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="vg-card__label" style={labelStyle}>
                Model
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="nova-3, gpt-4o, *"
                disabled={saving}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label className="vg-card__label" style={labelStyle}>
                Tenant
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                value={tenant}
                onChange={(e) => setTenant(e.target.value)}
                placeholder="(any)"
                disabled={saving}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label className="vg-card__label" style={labelStyle}>
                Plan
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                value={plan}
                onChange={(e) => setPlan(e.target.value)}
                placeholder="(any)"
                disabled={saving}
              />
            </div>
          </div>

          {kind === 'cost_plus' ? (
            <div>
              <label className="vg-card__label" style={labelStyle}>
                Markup (multiplier)
              </label>
              <input
                className="neo-input"
                style={fullWidth}
                type="number"
                step="0.01"
                value={markup}
                onChange={(e) => setMarkup(e.target.value)}
                disabled={saving}
              />
            </div>
          ) : (
            <div className="flex-row" style={{ gap: 12 }}>
              <div style={{ flex: 1 }}>
                <label className="vg-card__label" style={labelStyle}>
                  Price (USD)
                </label>
                <input
                  className="neo-input"
                  style={fullWidth}
                  type="number"
                  step="0.0001"
                  value={fixed}
                  onChange={(e) => setFixed(e.target.value)}
                  disabled={saving}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label className="vg-card__label" style={labelStyle}>
                  Unit
                </label>
                <select
                  className="neo-select"
                  style={fullWidth}
                  value={unit}
                  onChange={(e) => setUnit(e.target.value)}
                  disabled={saving}
                >
                  {UNITS.map((u) => (
                    <option key={u} value={u}>
                      {u}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>

        {error && (
          <div
            className="mt-md"
            style={{ color: 'var(--vg-red, #c0392b)', fontSize: 13, fontWeight: 500 }}
          >
            {error}
          </div>
        )}

        <div className="flex-row mt-lg" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="neo-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="neo-btn neo-btn--primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Add override'}
          </button>
        </div>
      </div>
    </div>
  );
}
