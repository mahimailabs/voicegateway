interface Props {
  text: string;
  rotation?: number;
}

export default function ProofChip({ text, rotation = -0.5 }: Props) {
  return (
    <span
      className="chip"
      style={{
        fontSize: 15,
        padding: '4px 14px',
        transform: `rotate(${rotation}deg)`,
        background: 'var(--paper)',
      }}
    >
      {text}
    </span>
  );
}
