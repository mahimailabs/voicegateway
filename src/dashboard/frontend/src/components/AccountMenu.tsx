import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import NavIcon from './NavIcon';
import { useTheme } from '../lib/useTheme';

interface Props {
  label: string;
  collapsed: boolean;
  onSignOut: () => void;
  hasToken: boolean;
}

/**
 * Bottom-of-sidebar account menu. Opens upward. This is the home for the
 * light/dark toggle (an account-level preference, not nav chrome) plus
 * Settings, API Keys, Docs, and Sign out.
 */
export default function AccountMenu({ label, collapsed, onSignOut, hasToken }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { theme, toggle } = useTheme();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="account" ref={ref}>
      {open && (
        <div className="account__menu" role="menu">
          <button
            type="button"
            className="account__item account__item--toggle"
            role="menuitemcheckbox"
            aria-checked={theme === 'dark'}
            onClick={toggle}
          >
            <span>Dark mode</span>
            <span className={`switch${theme === 'dark' ? ' switch--on' : ''}`} aria-hidden="true">
              <span className="switch__knob" />
            </span>
          </button>
          <div className="account__sep" />
          <Link className="account__item" to="/settings" role="menuitem" onClick={() => setOpen(false)}>
            <NavIcon id="settings" size={15} /> Settings
          </Link>
          <Link className="account__item" to="/api-keys" role="menuitem" onClick={() => setOpen(false)}>
            <NavIcon id="apikeys" size={15} /> API keys
          </Link>
          <a
            className="account__item"
            href="https://docs.voicegateway.dev"
            target="_blank"
            rel="noreferrer"
            role="menuitem"
          >
            <NavIcon id="docs" size={15} /> Docs <span className="account__ext">&#8599;</span>
          </a>
          {hasToken && (
            <>
              <div className="account__sep" />
              <button type="button" className="account__item account__item--danger" role="menuitem" onClick={onSignOut}>
                Sign out
              </button>
            </>
          )}
        </div>
      )}
      <button
        type="button"
        className="account__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        title={collapsed ? label : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="account__avatar"><NavIcon id="account" size={15} /></span>
        {!collapsed && (
          <>
            <span className="account__label">{label}</span>
            <span className="account__caret" aria-hidden="true">&#8942;</span>
          </>
        )}
      </button>
    </div>
  );
}
