import type { Metadata } from 'next';
import styles from './page.module.css';

export const metadata: Metadata = {
  title: 'About',
  description: 'A self-hosted voice agent SDK shipped from Sarnia, Ontario.',
};

export default function AboutPage() {
  return (
    <main className={styles.vgAbout}>
      <h1 className={`h-hand xxl ${styles.vgAboutTitle}`}>
        VoiceGateway is what we wish we had.
      </h1>
      <p className={`p-hand ${styles.vgAboutLead}`}>
        A self-hosted voice agent SDK for LiveKit. MIT, on your keys, no telemetry. Built so that what you ship in dev is what your users hear in production.
      </p>

      <div className={styles.vgAboutGrid}>
        <div className={`box soft ${styles.vgAboutCard}`}>
          <span className={`tiny-mono ${styles.vgAboutKicker}`}>WE ARE</span>
          <div className="h-hand">A Python SDK, an open repo, a CLI.</div>
        </div>
        <div className={`box soft ${styles.vgAboutCard}`}>
          <span className={`tiny-mono ${styles.vgAboutKicker}`}>WE ARE NOT</span>
          <div className="h-hand">A hosted service or a managed product.</div>
        </div>
      </div>
    </main>
  );
}
