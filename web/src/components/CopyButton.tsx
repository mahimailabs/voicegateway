'use client';
import { useState } from 'react';

interface Props {
  text: string;
}

export function CopyButton({ text }: Props) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      aria-label={copied ? 'Copied' : 'Copy command'}
      style={{
        fontFamily: 'var(--mono)',
        fontSize: 11,
        padding: '4px 10px',
        background: 'transparent',
        border: '1.2px solid var(--line-soft)',
        borderRadius: 4,
        color: copied ? 'var(--accent)' : 'var(--ink-soft)',
        cursor: 'pointer',
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        transition: 'border-color 120ms ease, color 120ms ease',
      }}
    >
      {copied ? 'copied' : 'copy'}
    </button>
  );
}
