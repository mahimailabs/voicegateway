import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useMatch } from 'react-router-dom';
import Overview from './pages/Overview';
import Models from './pages/Models';
import Costs from './pages/Costs';
import Latency from './pages/Latency';
import Logs from './pages/Logs';
import Projects from './pages/Projects';
import Settings from './pages/Settings';
import Login from './pages/Login';
import type { StatusResponse } from './lib/types';
import { fetchJson, getToken } from './lib/api';

const PAGES = [
  { to: '/',         label: 'Overview',  id: 'overview' },
  { to: '/models',   label: 'Models',    id: 'models'   },
  { to: '/costs',    label: 'Costs',     id: 'costs'    },
  { to: '/latency',  label: 'Latency',   id: 'latency'  },
  { to: '/logs',     label: 'Logs',      id: 'logs'     },
  { to: '/projects', label: 'Projects',  id: 'projects' },
  { to: '/settings', label: 'Settings',  id: 'settings' },
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
      .catch(() => {
        // If auth-status itself fails (older server, network), fall through
        // to the main app rather than locking the user out.
        setAuthState('ready');
      });
  }, []);

  useEffect(() => {
    if (authState !== 'ready') return;
    fetchJson<StatusResponse>('/api/status').then(setStatus).catch(() => setStatus(null));
  }, [authState]);

  if (authState === 'checking') return null;
  if (authState === 'needs-login') {
    return <Login onAuthed={() => setAuthState('ready')} />;
  }

  return (
    <BrowserRouter>
      <div className="app-shell">
        <Sidebar status={status} />
        <main className="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/models" element={<Models />} />
            <Route path="/costs" element={<Costs />} />
            <Route path="/latency" element={<Latency />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/settings/audit-log" element={<Settings tab="audit" />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function Sidebar({ status }: { status: StatusResponse | null }) {
  const providerCount = status ? Object.keys(status.providers).length : 0;
  const modelCount = status ? Object.keys(status.models).length : 0;

  return (
    <aside className="sidebar">
      <div className="sidebar__logo">
        VoiceGateway
        <small>SELF-HOSTED VOICE AI</small>
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
        <span className="version-pill">v0.1.0</span>
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
