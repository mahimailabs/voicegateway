import parse from 'html-react-parser';
import { renderMermaidSVG } from 'beautiful-mermaid';

interface MermaidProps {
  chart: string;
}

export function Mermaid({ chart }: MermaidProps) {
  let svg: string;
  try {
    svg = renderMermaidSVG(chart, {
      bg: 'transparent',
      fg: '#2a2a2a',
      line: '#cdb59d',
      accent: '#c46a3a',
      muted: '#7a6a55',
      surface: '#f4ecdf',
      border: '#cdb59d',
      font: 'var(--font-inter, Inter), system-ui, sans-serif',
      transparent: true,
      padding: 24,
    });
  } catch {
    return (
      <pre className="overflow-x-auto rounded-lg border border-fd-border bg-fd-muted p-4 text-sm">
        <code>{chart}</code>
      </pre>
    );
  }

  return (
    <div className="vg-mermaid my-6 overflow-x-auto rounded-lg border border-fd-border bg-fd-card p-4">
      {parse(svg)}
    </div>
  );
}
