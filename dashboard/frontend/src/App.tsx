import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useMatch } from 'react-router-dom';
import Overview from './pages/Overview';
import Models from './pages/Models';
import Costs from './pages/Costs';
import Latency from './pages/Latency';
import Logs from './pages/Logs';
import Metrics from './pages/Metrics';
import Replay from './pages/Replay';
import Sessions from './pages/Sessions';
import Projects from './pages/Projects';
import Providers from './pages/Providers';
import Routing from './pages/Routing';
import Guardrails from './pages/Guardrails';
import Settings from './pages/Settings';
import VirtualKeys from './pages/VirtualKeys';
import Login from './pages/Login';
import type { StatusResponse } from './lib/types';
import { AUTH_REQUIRED_EVENT, clearToken, fetchJson, getToken } from './lib/api';
import { applyBrandingForProject } from './lib/branding';

interface ActiveBranding {
  logo_url?: string | null;
  accent_color?: string | null;
  product_name?: string | null;
}

// Foundry v0.4.0 nav order: Costs, Logs, Sessions, Metrics, Replay
// (conditional, lives under /sessions/:id/replay so it is not a top-
// level entry), Projects, Providers, Virtual Keys. Overview, Models,
// Latency, and Settings predate that spec and are kept above /
// below the v0.4.0 block so existing users find them where they were.
// Foundry v0.5.0 nav order: Costs, Logs, Sessions, Metrics, Replay
// (conditional, lives under /sessions/:id/replay so it is not a top-
// level entry), Routing, Projects, Providers, Virtual Keys, Settings.
// Overview, Models, and Latency predate that spec and are kept above
// the v0.5.0 block so existing users find them where they were.
const PAGES = [
  { to: '/',             label: 'Overview',     id: 'overview' },
  { to: '/models',       label: 'Models',       id: 'models'   },
  { to: '/costs',        label: 'Costs',        id: 'costs'    },
  { to: '/latency',      label: 'Latency',      id: 'latency'  },
  { to: '/logs',         label: 'Logs',         id: 'logs'     },
  { to: '/sessions',     label: 'Sessions',     id: 'sessions' },
  { to: '/metrics',      label: 'Metrics',      id: 'metrics'  },
  { to: '/routing',      label: 'Routing',      id: 'routing'  },
  { to: '/guardrails',   label: 'Guardrails',   id: 'guardrails' },
  { to: '/projects',     label: 'Projects',     id: 'projects' },
  { to: '/providers',    label: 'Providers',    id: 'providers' },
  { to: '/virtual-keys', label: 'Virtual Keys', id: 'virtual-keys' },
  { to: '/settings',     label: 'Settings',     id: 'settings' },
] as const;

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
        // If auth-status itself fails (older server, network), fall
        // through to the main app rather than locking the user out.
        // Log so a 500-looking bootstrap is still debuggable.
        console.warn('auth-status check failed; proceeding without gate', err);
        setAuthState('ready');
      });
  }, []);

  // Server-side 401/403 → token's expired/revoked → show login again.
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

  // v0.5.0: apply per-project white-label branding once on layout
  // mount (REQ-VG-ROUTE-004). Read-once-per-mount per OQ5. Reads the
  // ?project=<id> URL param; a downstream operator who wants the
  // branded chrome on every page sets the param at link time.
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
        <main className="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/models" element={<Models />} />
            <Route path="/costs" element={<Costs />} />
            <Route path="/latency" element={<Latency />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:sessionId/replay" element={<Replay />} />
            <Route path="/metrics" element={<Metrics />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/routing" element={<Routing />} />
            <Route path="/guardrails" element={<Guardrails />} />
            <Route path="/virtual-keys" element={<VirtualKeys />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/settings/audit-log" element={<Settings tab="audit" />} />
          </Routes>
        </main>
      </div>
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
  const productName = branding?.product_name || 'VoiceGateway';
  const logoUrl = branding?.logo_url || null;

  return (
    <aside className="sidebar">
      <div className="sidebar__logo">
        {logoUrl && (
          <img
            src={logoUrl}
            alt={`${productName} logo`}
            style={{ maxHeight: '32px', maxWidth: '160px', display: 'block', marginBottom: '0.25rem' }}
          />
        )}
        {productName}
        <small>{branding?.product_name ? 'WHITE-LABEL' : 'SELF-HOSTED VOICE AI'}</small>
      </div>
      <nav className="sidebar__nav">
        {PAGES.map((p) => (
          <SidebarNavItem key={p.id} to={p.to} id={p.id} label={p.label} />
        ))}
      </nav>
      <div className="sidebar__footer">
        <div className="sidebar__footer-label">Status</div>
        <div className="sidebar__status-row">
          <span className="neo-status-dot neo-status-dot--online" />
          Gateway Online
        </div>
        <div className="sidebar__status-row">
          <span className="neo-status-dot neo-status-dot--online" />
          {providerCount} Providers · {modelCount} Models
        </div>
        <span className="version-pill">v0.5.0</span>
        {hasToken && (
          <button
            type="button"
            className="neo-btn mt-sm"
            style={{ width: '100%' }}
            onClick={onSignOut}
          >
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}

function SidebarNavItem({ to, id, label }: { to: string; id: string; label: string }) {
  const isRoot = to === '/';
  const match = useMatch({ path: to, end: isRoot });
  const active = !!match;

  return (
    <NavLink
      to={to}
      end={isRoot}
      className={`nav-item nav-item--${id}${active ? ' nav-item--active' : ''}`}
    >
      {label}
    </NavLink>
  );
}
