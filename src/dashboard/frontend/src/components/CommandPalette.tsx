import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import NavIcon from './NavIcon';
import { NAV_FLAT } from '../lib/nav';

/** Cmd/Ctrl+K command palette: fuzzy-jump between pages. */
export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const results = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return NAV_FLAT;
    return NAV_FLAT.filter(
      (i) => i.label.toLowerCase().includes(t) || i.group.toLowerCase().includes(t),
    );
  }, [q]);

  useEffect(() => {
    setSel(0);
  }, [q]);

  useEffect(() => {
    if (open) {
      setQ('');
      setSel(0);
      const id = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(id);
    }
  }, [open]);

  if (!open) return null;

  const go = (i: number) => {
    const item = results[i];
    if (item) {
      navigate(item.to);
      setOpen(false);
    }
  };

  // Handle keys on the dialog container so navigation keeps working after
  // focus moves to a result button, and trap Tab inside the open palette.
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(sel);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === 'Tab') {
      const nodes = dialogRef.current?.querySelectorAll<HTMLElement>('input, button');
      if (!nodes || nodes.length === 0) return;
      const list = Array.from(nodes);
      const first = list[0];
      const last = list[list.length - 1];
      const activeEl = document.activeElement;
      if (e.shiftKey && activeEl === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    <div className="cmdk-backdrop" onMouseDown={() => setOpen(false)}>
      <div
        ref={dialogRef}
        className="cmdk"
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="cmdk__input"
          placeholder="Search pages..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="cmdk__list">
          {results.length === 0 && <div className="cmdk__empty">No matches</div>}
          {results.map((item, i) => (
            <button
              key={item.to}
              type="button"
              className={`cmdk__item${i === sel ? ' cmdk__item--active' : ''}`}
              onMouseEnter={() => setSel(i)}
              onClick={() => go(i)}
            >
              <NavIcon id={item.id} size={16} />
              <span className="cmdk__itemlabel">{item.label}</span>
              <span className="cmdk__group">{item.group}</span>
            </button>
          ))}
        </div>
        <div className="cmdk__hint">
          <kbd>&#8593;&#8595;</kbd> navigate <kbd>&#8629;</kbd> open <kbd>esc</kbd> close
        </div>
      </div>
    </div>
  );
}
