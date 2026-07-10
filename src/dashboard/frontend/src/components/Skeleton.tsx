import type { CSSProperties } from 'react';

/** A sized loading placeholder. Match it to the real content to avoid layout shift. */
export function Skeleton({
  width,
  height,
  radius,
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  style?: CSSProperties;
}) {
  return <div className="skeleton" style={{ width, height, borderRadius: radius, ...style }} />;
}

/** Loading placeholder shaped like a KPI / StatusCard. */
export function StatCardSkeleton() {
  return (
    <div className="vg-card">
      <Skeleton width={88} height={12} />
      <Skeleton width={120} height={30} style={{ marginTop: 16 }} />
    </div>
  );
}
