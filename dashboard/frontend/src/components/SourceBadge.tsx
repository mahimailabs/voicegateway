interface Props {
  source: string;
}

const STYLES: Record<string, { bg: string; label: string }> = {
  yaml:  { bg: '#888888', label: 'YAML' },
  db:    { bg: '#8AC926', label: 'Custom' },
  env:   { bg: '#118AB2', label: 'ENV' },
  auto:  { bg: '#7B2FF7', label: 'Auto' },
  api:   { bg: '#FB8500', label: 'API' },
  mcp:   { bg: '#FF006E', label: 'MCP' },
};

export default function SourceBadge({ source }: Props) {
  const style = STYLES[source] ?? { bg: '#aaaaaa', label: 'Unknown' };
  return (
    <span
      className="neo-badge"
      style={{
        backgroundColor: style.bg,
        color: '#fff',
        fontWeight: 600,
        fontSize: 11,
        letterSpacing: '0.05em',
      }}
    >
      {style.label}
    </span>
  );
}
