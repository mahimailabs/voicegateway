import HeroBlock from '../../components/HeroBlock';
import PromiseCard from '../../components/PromiseCard';
import CapabilityCard from '../../components/CapabilityCard';
import CtaBand from '../../components/CtaBand';
import styles from './page.module.css';

const promises = [
  { num: '01', title: 'Your code', body: 'source day one' },
  { num: '02', title: 'Your cloud', body: 'your infra, your bill' },
  { num: '03', title: 'Your keys', body: 'providers billed to you' },
];

const capabilities = [
  {
    fnum: 'F1',
    title: 'Cost transparency',
    body: 'See cost per call, per token, per TTS character. To the cent, never rounded.',
    href: '/docs/guides/cost-transparency',
    status: 'shipped',
  },
  {
    fnum: 'F2',
    title: 'Voice-conversation metrics',
    body: 'Latency, interruptions, dead air. The numbers your stack hides by default.',
    href: '/docs/guides/voice-metrics',
    status: 'v0.2',
  },
  {
    fnum: 'F3',
    title: 'Conversation replay',
    body: 'Scrub any past call. Hear what the model heard, see what it said.',
    href: '/docs/guides/replay',
    status: 'v0.3',
  },
  {
    fnum: 'F4',
    title: 'Multi-tenant attribution',
    body: 'One SDK, many tenants. Bills line up with brands. No shared keys.',
    href: '/docs/guides/multi-tenant',
    status: 'v0.4',
  },
  {
    fnum: 'F5',
    title: 'Cross-modality routing',
    body: 'Voice in, chat out. Or text in, phone callback. You pick the shape.',
    href: '/docs/guides/routing',
    status: 'v0.5',
  },
  {
    fnum: 'F6',
    title: 'Voice guardrails',
    body: 'Block PII, refuse off-topic, fall back when the model wanders mid-call.',
    href: '/docs/guides/guardrails',
    status: 'v0.6',
  },
];

export default function HomePage() {
  return (
    <main>
      <HeroBlock />

      <div className={styles.vgDottedRule} aria-hidden="true" />

      <section
        className={`${styles.vgSection} ${styles.vgPromises}`}
        aria-labelledby="vg-promises-h"
      >
        <h2 id="vg-promises-h" className={styles.srOnly}>
          The promise
        </h2>
        <div className={styles.vgPromisesGrid}>
          {promises.map((p) => (
            <PromiseCard key={p.num} num={p.num} title={p.title} body={p.body} />
          ))}
        </div>
      </section>

      <div className={styles.vgDottedRule} aria-hidden="true" />

      <section className={styles.vgSection} aria-labelledby="vg-caps-h">
        <div className={styles.vgSectionHead}>
          <span className={styles.vgCapsKicker}>Capabilities · v0.1</span>
          <h2 id="vg-caps-h" className={`h-hand ${styles.vgCapsH}`}>
            Six capabilities. One SDK.
            <br />
            Shipping across v0.1 to v0.6.
          </h2>
        </div>
        <div className={styles.vgCapsGrid}>
          {capabilities.map((c) => (
            <CapabilityCard
              key={c.fnum}
              fnum={c.fnum}
              title={c.title}
              body={c.body}
              href={c.href}
              status={c.status}
            />
          ))}
        </div>
      </section>

      <CtaBand />
    </main>
  );
}
