import Link from 'next/link';
import styles from './CtaBand.module.css';

export default function CtaBand() {
  return (
    <section className={styles.vgCtaBand} aria-labelledby="vg-cta-h">
      <div className={`box accent ${styles.vgCtaInner}`}>
        <p id="vg-cta-h" className={`h-hand ${styles.vgCtaLine}`}>
          The voice infra layer you can self-host, read, and trust.
        </p>
        <Link className={`btn solid ${styles.vgCtaBtn}`} href="/docs">
          Read the docs <span aria-hidden="true">→</span>
        </Link>
      </div>
    </section>
  );
}
