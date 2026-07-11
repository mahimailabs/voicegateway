// The VoiceGateway brand mark: the gauge logo, white, on the teal gradient
// square used in the sidebar header (collapsed + expanded).

export default function BrandMark({
  size = 26,
  label,
}: {
  size?: number;
  label?: string;
}) {
  const glyph = Math.round(size * 0.66);
  return (
    <span
      className="brand-mark"
      aria-hidden={label ? undefined : true}
      aria-label={label}
      style={{
        width: size,
        height: size,
        borderRadius: 8,
        flexShrink: 0,
        background: 'linear-gradient(135deg, var(--vg-teal-bright), var(--vg-teal-deep))',
        boxShadow:
          '0 2px 6px -1px rgba(21,120,138,0.5), inset 0 1px 0 rgba(255,255,255,0.25)',
        display: 'grid',
        placeItems: 'center',
      }}
    >
      <svg width={glyph} height={glyph} viewBox="104 176 472 300" fill="none" aria-hidden="true">
        <path
          d="M140 420 A200 200 0 0 1 540 420"
          stroke="rgba(255,255,255,0.35)"
          strokeWidth="44"
          strokeLinecap="round"
        />
        <path
          d="M140 420 A200 200 0 0 1 283 198"
          stroke="#ffffff"
          strokeWidth="44"
          strokeLinecap="round"
        />
        <line
          x1="340"
          y1="420"
          x2="238"
          y2="206"
          stroke="#ffffff"
          strokeWidth="22"
          strokeLinecap="round"
        />
        <circle cx="340" cy="420" r="34" fill="#ffffff" />
        <circle cx="340" cy="420" r="16" fill="var(--vg-teal-deep)" />
      </svg>
    </span>
  );
}
