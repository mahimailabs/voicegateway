import styles from './CapabilityCard.module.css';

interface Props {
  fnum: string;
  title: string;
  body: string;
  href?: string;
  status: string;
}

export default function CapabilityCard({ fnum, title, body, href = '#', status }: Props) {
  const isShipped = status === 'shipped';
  const pillLabel = isShipped ? 'SHIPPED · v0.1' : `SHIPPING · ${status}`;
  const pillAria = isShipped ? 'Shipped in v0.1' : `Shipping in ${status}`;

  return (
    <article className={`ab ${styles.vgCap}`}>
      <span
        className={styles.vgCapStatus}
        data-shipped={String(isShipped)}
        aria-label={pillAria}
      >
        {pillLabel}
      </span>
      <header className="ab-head">
        <span className="label">{fnum}</span>
        <span className="meta">{title}</span>
      </header>
      <div className={`ab-body ${styles.vgCapBody}`}>
        <p className={`p-hand ${styles.vgCapLine}`}>{body}</p>
        <a className={styles.vgCapLink} href={href} aria-label={`Read the docs for ${title}`}>
          docs <span aria-hidden="true">→</span>
        </a>
      </div>
    </article>
  );
}
