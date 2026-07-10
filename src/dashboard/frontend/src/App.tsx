import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useMatch } from 'react-router-dom';
import Overview from './pages/Overview';
import Costs from './pages/Costs';
import Logs from './pages/Logs';
import Replay from './pages/Replay';
import Sessions from './pages/Sessions';
import Agents from './pages/Agents';
import Settings from './pages/Settings';
import Login from './pages/Login';
import Latency from './pages/Latency';
import Metrics from './pages/Metrics';
import Models from './pages/Models';
import Providers from './pages/Providers';
import Routing from './pages/Routing';
import Projects from './pages/Projects';
import ApiKeys from './pages/ApiKeys';
import type { StatusResponse } from './lib/types';
import { AUTH_REQUIRED_EVENT, clearToken, fetchJson, getToken } from './lib/api';
import { applyBrandingForProject } from './lib/branding';
import NavIcon from './components/NavIcon';
import AccountMenu from './components/AccountMenu';
import CommandPalette from './components/CommandPalette';
import { NAV, type NavLeaf } from './lib/nav';

interface ActiveBranding {
  logo_url?: string | null;
  accent_color?: string | null;
  product_name?: string | null;
}

type AuthState = 'checking' | 'needs-login' | 'ready';

export default function App() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [authState, setAuthState] = useState<AuthState>('checking');

  useEffect(() => {
    fetchJson<{ auth_required: boolean }>('/api/auth-status')
      .then(({ auth_required }) => {
        if (auth_required && !getToken()) {
          setAuthState('needs-login');
        } else {
          setAuthState('ready');
        }
      })
      .catch((err) => {
        console.warn('auth-status check failed; proceeding without gate', err);
        setAuthState('ready');
      });
  }, []);

  useEffect(() => {
    const onAuthRequired = () => setAuthState('needs-login');
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setStatus(null);
    setAuthState('needs-login');
  }, []);

  useEffect(() => {
    if (authState !== 'ready') return;
    fetchJson<StatusResponse>('/api/status').then(setStatus).catch(() => setStatus(null));
  }, [authState]);

  const [branding, setBranding] = useState<ActiveBranding | null>(null);
  useEffect(() => {
    if (authState !== 'ready') return;
    const params = new URLSearchParams(window.location.search);
    const projectId = params.get('project');
    applyBrandingForProject(projectId).then(setBranding).catch(() => setBranding(null));
  }, [authState]);

  if (authState === 'checking') return null;
  if (authState === 'needs-login') {
    return <Login onAuthed={() => setAuthState('ready')} />;
  }

  return (
    <BrowserRouter>
      <div className="app-shell">
        <Sidebar status={status} onSignOut={signOut} branding={branding} />
        <div className="main">
          <main className="main-content">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/sessions" element={<Sessions />} />
              <Route path="/sessions/:sessionId/replay" element={<Replay />} />
              <Route path="/costs" element={<Costs />} />
              <Route path="/latency" element={<Latency />} />
              <Route path="/metrics" element={<Metrics />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/providers" element={<Providers />} />
              <Route path="/models" element={<Models />} />
              <Route path="/routing" element={<Routing />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/api-keys" element={<ApiKeys />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/audit-log" element={<Settings tab="audit" />} />
            </Routes>
          </main>
        </div>
      </div>
      <CommandPalette />
    </BrowserRouter>
  );
}

function Sidebar({
  status,
  onSignOut,
  branding,
}: {
  status: StatusResponse | null;
  onSignOut: () => void;
  branding: ActiveBranding | null;
}) {
  const providerCount = status ? Object.keys(status.providers).length : 0;
  const modelCount = status ? Object.keys(status.models).length : 0;
  const hasToken = !!getToken();
  const isWhiteLabel = !!branding?.product_name;
  const productName = branding?.product_name || 'VoiceGateway';

  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem('vg-sidebar-collapsed') === '1',
  );

  useEffect(() => {
    document.documentElement.style.setProperty('--sidebar-width', collapsed ? '60px' : 'var(--vg-sidebar-w)');
    localStorage.setItem('vg-sidebar-collapsed', collapsed ? '1' : '0');
  }, [collapsed]);

  // Cmd/Ctrl+B toggles the rail (VS Code / Linear muscle memory).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <aside className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      {/* Brand header */}
      <div className="sidebar__header">
        {collapsed ? (
          /* Collapsed: show a teal brand-mark square */
          <span
            className="brand-mark"
            aria-label={productName}
            style={{
              width: 28, height: 28, borderRadius: 8, flexShrink: 0,
              background: 'linear-gradient(135deg, var(--vg-teal-bright), var(--vg-teal-deep))',
              boxShadow: '0 2px 6px -1px rgba(21,120,138,0.5), inset 0 1px 0 rgba(255,255,255,0.25)',
              display: 'grid', placeItems: 'center',
            }}
          />
        ) : isWhiteLabel ? (
          <div className="sidebar__brand">
            {branding?.logo_url && <img src={branding.logo_url} alt={`${productName} logo`} className="sidebar__mark" />}
            <span className="sidebar__brandname">{productName}</span>
          </div>
        ) : (
          <div className="sidebar__brand" style={{ gap: 10 }}>
            {/* Teal gradient brand-mark */}
            <span
              className="brand-mark"
              aria-hidden="true"
              style={{
                width: 26, height: 26, borderRadius: 8, flexShrink: 0,
                background: 'linear-gradient(135deg, var(--vg-teal-bright), var(--vg-teal-deep))',
                boxShadow: '0 2px 6px -1px rgba(21,120,138,0.5), inset 0 1px 0 rgba(255,255,255,0.25)',
                display: 'grid', placeItems: 'center',
              }}
            />
            <span className="sidebar__brandname">VoiceGateway</span>
          </div>
        )}
        <button
          type="button"
          className="sidebar__collapse"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={`${collapsed ? 'Expand' : 'Collapse'} (Cmd/Ctrl+B)`}
          onClick={() => setCollapsed((c) => !c)}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M9 4v16" />
          </svg>
        </button>
      </div>

      <nav className="sidebar__nav">
        {NAV.map((entry) =>
          'group' in entry ? (
            <div className="nav-group" key={entry.group}>
              {!collapsed && <div className="nav-group__label">{entry.group}</div>}
              {entry.items.map((it) => (
                <SidebarNavItem key={it.id} {...it} collapsed={collapsed} />
              ))}
            </div>
          ) : (
            <SidebarNavItem key={entry.id} {...entry} collapsed={collapsed} />
          ),
        )}
      </nav>

      <div className="sidebar__footer">
        {!collapsed && (
          <a className="nav-item nav-item--util" href="https://docs.voicegateway.dev" target="_blank" rel="noreferrer" style={{ color: 'var(--vg-muted)' }}>
            <NavIcon id="docs" />
            <span className="nav-item__label">Docs</span>
            <span className="nav-item__ext" aria-hidden="true">&#8599;</span>
          </a>
        )}
        {!collapsed && (
          <div className="sidebar__status" title={`${providerCount} providers, ${modelCount} models`}>
            <span className="neo-status-dot neo-status-dot--online" />
            Gateway online
            {status?.version && <span className="version-pill">v{status.version}</span>}
          </div>
        )}
        <AccountMenu label={productName} collapsed={collapsed} onSignOut={onSignOut} hasToken={hasToken} />
      </div>
    </aside>
  );
}

function SidebarNavItem({ to, id, label, collapsed }: NavLeaf & { collapsed: boolean }) {
  const isRoot = to === '/';
  const match = useMatch({ path: to, end: isRoot });
  const active = !!match;

  return (
    <NavLink
      to={to}
      end={isRoot}
      className={`nav-item${active ? ' nav-item--active' : ''}`}
      title={collapsed ? label : undefined}
    >
      <NavIcon id={id} />
      <span className="nav-item__label">{label}</span>
    </NavLink>
  );
}
