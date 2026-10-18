import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Privacy',
  description:
    'What VoiceGateway collects, why, and how to remove it. We collect a waitlist email to contact you about beta access. The website is separate from the VoiceGateway observability platform.',
};

const UPDATED = 'October 2026';
const CONTACT = 'mahi@mahimai.ca';

export default function PrivacyPage() {
  return (
    <main
      id="main-content"
      style={{
        maxWidth: 768,
        margin: '0 auto',
        padding: '80px 24px',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* Page title */}
      <h1
        style={{
          fontSize: 'clamp(32px, 5vw, 48px)',
          fontWeight: 600,
          letterSpacing: '-0.02em',
          lineHeight: 1.06,
          color: 'var(--color-ink)',
          margin: '0 0 20px',
          fontFamily: 'var(--font-sans)',
        }}
      >
        Privacy
      </h1>

      {/* Lead */}
      <p
        style={{
          fontSize: 17,
          lineHeight: 1.7,
          color: 'var(--color-muted)',
          margin: '0 0 48px',
          maxWidth: 600,
        }}
      >
        The short version: we collect the email you give us to join the waitlist so we can tell you
        when access opens. We do not sell your waitlist email. This page describes the marketing website.
      </p>

      {/* Sections */}
      <PrivSection heading="What we collect">
        <ul
          style={{
            margin: 0,
            padding: '0 0 0 20px',
            display: 'grid',
            gap: 8,
          }}
        >
          <li style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)' }}>
            <strong style={{ color: 'var(--color-ink)', fontWeight: 600 }}>Waitlist email.</strong>{' '}
            When you submit the form on this site, we store the email address you enter, plus the
            timestamp and page it came from.
          </li>
          <li style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)' }}>
            <strong style={{ color: 'var(--color-ink)', fontWeight: 600 }}>
              Website analytics.
            </strong>{' '}
            PostHog measures visits, documentation and source links, and successful waitlist confirmations. On docs.voicegateway.dev it also counts searches, code copies, outbound links and the answers to “Was this page helpful?”. Analytics runs only on the production sites, uses in-memory identifiers, and does not record sessions or send your email address. No account is required to browse this site.
          </li>
        </ul>
      </PrivSection>

      <PrivSection heading="Why we collect your waitlist email">
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)', margin: 0 }}>
          We use your waitlist email to contact you about VoiceGateway: to let you know when access is available and, occasionally, to send related product updates.
        </p>
      </PrivSection>

      <PrivSection heading="Where it is stored, and who sees it">
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)', margin: 0 }}>
          Waitlist emails are stored in the configured Postgres database and are visible to the
          VoiceGateway team. A configured email service also sends a signup notification to the team. We do not sell your email or use it for advertising.
        </p>
      </PrivSection>

      <PrivSection heading="Your call audio">
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)', margin: 0 }}>
          The marketing website does not receive your agent’s calls. The V1 product is designed to store call telemetry and transcripts, with recordings enabled only when a project opts in. Self-hosted data stays on your infrastructure; Cloud data will be stored in your hosted workspace. VoiceGateway does not sit in the live audio path.
        </p>
      </PrivSection>

      <PrivSection heading="Your choices">
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)', margin: 0 }}>
          You can ask us to show you, or delete, the data we hold about you at any time. Email{' '}
          <a
            href={`mailto:${CONTACT}`}
            style={{
              color: 'var(--color-accent-deep)',
              textDecoration: 'underline',
              textUnderlineOffset: 3,
            }}
          >
            {CONTACT}
          </a>{' '}
          and we will remove your waitlist entry promptly.
        </p>
      </PrivSection>

      <PrivSection heading="Contact">
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--color-muted)', margin: 0 }}>
          Questions about privacy? Reach us at{' '}
          <a
            href={`mailto:${CONTACT}`}
            style={{
              color: 'var(--color-accent-deep)',
              textDecoration: 'underline',
              textUnderlineOffset: 3,
            }}
          >
            {CONTACT}
          </a>
          .
        </p>
      </PrivSection>

      <p
        style={{
          marginTop: 48,
          fontSize: 13,
          color: 'var(--color-faint)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        Last updated: {UPDATED}
      </p>
    </main>
  );
}

function PrivSection({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        marginBottom: 36,
        paddingBottom: 36,
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      <h2
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          fontWeight: 500,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--color-faint)',
          margin: '0 0 12px',
        }}
      >
        {heading}
      </h2>
      {children}
    </section>
  );
}
