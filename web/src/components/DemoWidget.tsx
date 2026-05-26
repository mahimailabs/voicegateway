'use client';

import { FormEvent, useEffect, useState } from 'react';
import { parsePhoneNumberFromString, type CountryCode } from 'libphonenumber-js';
import CostRow, { type CostRowResult } from './CostRow';
import styles from './DemoWidget.module.css';

type Placement = 'docs';
type WidgetState = 'idle' | 'verifying' | 'awaitingCode' | 'calling' | 'onCall' | 'results';

export type DemoWidgetProps = {
  placement?: Placement;
  title?: string;
  body?: string;
};

const COUNTRIES: Array<{
  label: string;
  dialCode: string;
  phoneCountry: CountryCode;
}> = [
  { label: 'US', dialCode: '+1', phoneCountry: 'US' },
  { label: 'Canada', dialCode: '+1', phoneCountry: 'CA' },
  { label: 'India', dialCode: '+91', phoneCountry: 'IN' },
  { label: 'UK', dialCode: '+44', phoneCountry: 'GB' },
];

const MOCK_CODE = '042610';
const DEFAULT_TITLE = 'Feel the demo flow before installing.';
const DEFAULT_BODY =
  'This docs widget is a local mock of the planned live demo: it validates a test phone number, walks through verification, simulates a short call, and renders a VoiceGateway-style cost row. No SMS is sent and no call is placed.';

const MOCK_RESULT: CostRowResult = {
  callId: 'mock-call-docs',
  sessionId: 'mock-session-docs',
  currency: 'USD',
  totalUsd: 0.04,
  rows: [
    {
      modality: 'stt',
      provider: 'deepgram',
      model: 'nova-3',
      units: 0.95,
      unitLabel: 'audio min',
      subtotalUsd: 0.01,
    },
    {
      modality: 'llm',
      provider: 'openai',
      model: 'gpt-4.1-mini',
      inputUnits: 1320,
      outputUnits: 220,
      subtotalUsd: 0.02,
    },
    {
      modality: 'tts',
      provider: 'cartesia',
      model: 'sonic',
      units: 860,
      unitLabel: 'characters',
      subtotalUsd: 0.01,
    },
  ],
};

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default function DemoWidget({
  placement = 'docs',
  title = DEFAULT_TITLE,
  body = DEFAULT_BODY,
}: DemoWidgetProps) {
  const [phase, setPhase] = useState<WidgetState>('idle');
  const [country, setCountry] = useState(COUNTRIES[0]);
  const [phoneInput, setPhoneInput] = useState('415 555 0132');
  const [displayName, setDisplayName] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('Use any valid-looking number. The mock code is shown inline.');
  const [wrongCodes, setWrongCodes] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<CostRowResult | null>(null);

  useEffect(() => {
    if (phase !== 'onCall') return;

    const timer = window.setInterval(() => {
      setElapsed((current) => Math.min(current + 10, 60));
    }, 700);

    return () => window.clearInterval(timer);
  }, [phase]);

  function normalizedPhone() {
    const parsed = parsePhoneNumberFromString(phoneInput, country.phoneCountry);
    if (!parsed?.isValid()) return null;
    return parsed.number;
  }

  async function handleVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const phone = normalizedPhone();
    if (!phone) {
      setMessage('That number does not look valid for the selected country.');
      return;
    }

    setResult(null);
    setWrongCodes(0);
    setPhase('verifying');
    setMessage('Mocking Telnyx verification locally. No SMS is being sent.');
    await sleep(500);
    setPhase('awaitingCode');
    setMessage(`Enter mock code ${MOCK_CODE}. This stays entirely in your browser.`);
  }

  async function handleCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code !== MOCK_CODE) {
      const attempts = wrongCodes + 1;
      setWrongCodes(attempts);
      setMessage(
        attempts >= 3
          ? `Three wrong attempts would cool down for 60 seconds in production. Use ${MOCK_CODE} to continue the mock.`
          : `Wrong mock code. Use ${MOCK_CODE} to continue.`,
      );
      return;
    }

    setPhase('calling');
    setMessage('Simulating the production call setup locally. No LiveKit or Telnyx credentials are used.');
    await sleep(650);
    setElapsed(0);
    setPhase('onCall');
    setMessage(
      displayName.trim()
        ? `Mock call connected for ${displayName.trim()}. A real demo would wrap at 60 seconds.`
        : 'Mock call connected. A real demo would wrap at 60 seconds.',
    );
    await sleep(4500);
    setResult(MOCK_RESULT);
    setPhase('results');
    setMessage('Mock cost row rendered. Production would use VoiceGateway session records.');
  }

  function reset() {
    setPhase('idle');
    setCode('');
    setElapsed(0);
    setMessage('Use any valid-looking number. The mock code is shown inline.');
  }

  function dismissResult() {
    setResult(null);
    reset();
  }

  const headingId = `vg-demo-${placement}-h`;
  const busy = phase === 'verifying' || phase === 'calling' || phase === 'onCall';
  const showCodeForm = phase === 'awaitingCode';
  const showCallProgress = phase === 'calling' || phase === 'onCall';
  const showTimer = phase === 'onCall';
  const showResult = phase === 'results' && result;

  return (
    <section id="live-demo" className={styles.vgDemo} aria-labelledby={headingId}>
      <div className={styles.vgDemoShell}>
        <div className={styles.vgDemoIntro}>
          <div>
            <p className={styles.vgDemoKicker}>Docs demo / mocked locally</p>
            <h2 id={headingId} className={styles.vgDemoTitle}>
              {title}
            </h2>
            <p className={styles.vgDemoBody}>{body}</p>
          </div>
          <div className={styles.vgDemoNotes} aria-label="Demo constraints">
            <span className="chip">no SMS</span>
            <span className="chip">no call</span>
            <span className="chip">mock cost row</span>
          </div>
        </div>

        <div className={styles.vgDemoPanel}>
          {showResult ? (
            <div className={styles.vgDemoResult}>
              <CostRow result={result} />
              <div className={styles.vgDemoActions}>
                <button className={`btn accent ${styles.vgDemoButton}`} type="button" onClick={reset}>
                  run mock again
                </button>
                <button className={styles.vgDemoSecondary} type="button" onClick={dismissResult}>
                  dismiss result
                </button>
              </div>
            </div>
          ) : showCallProgress ? (
            <div className={styles.vgDemoForm}>
              <div className={styles.vgDemoTimer} aria-label="Mock call timer">
                <div className={styles.vgDemoTimerTrack}>
                  <div className={styles.vgDemoTimerFill} style={{ width: `${(elapsed / 60) * 100}%` }} />
                </div>
                <p className={styles.vgDemoStatus}>
                  {showTimer ? `${elapsed}s / 60s simulated` : 'dialing...'}
                </p>
              </div>
              <p className={styles.vgDemoStatus} aria-live="polite">
                {message}
              </p>
            </div>
          ) : showCodeForm ? (
            <form className={styles.vgDemoForm} onSubmit={handleCode}>
              <label className={styles.vgDemoLabel}>
                Verification code
                <input
                  className={`${styles.vgDemoInput} ${styles.vgDemoCode}`}
                  inputMode="numeric"
                  maxLength={6}
                  pattern="[0-9]{6}"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                  autoComplete="one-time-code"
                />
              </label>
              <p className={styles.vgDemoStatus} aria-live="polite">
                {message}
              </p>
              <div className={styles.vgDemoActions}>
                <button className={`btn accent ${styles.vgDemoButton}`} type="submit">
                  continue mock
                </button>
                <button className={styles.vgDemoSecondary} type="button" onClick={reset}>
                  change number
                </button>
              </div>
            </form>
          ) : (
            <form className={styles.vgDemoForm} onSubmit={handleVerify}>
              <label className={styles.vgDemoLabel}>
                Name, optional
                <input
                  className={styles.vgDemoInput}
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  autoComplete="given-name"
                  placeholder="Ada"
                />
              </label>

              <label className={styles.vgDemoLabel}>
                Phone number
                <div className={styles.vgDemoInputRow}>
                  <select
                    className={styles.vgDemoSelect}
                    value={country.label}
                    onChange={(event) => {
                      const next = COUNTRIES.find((item) => item.label === event.target.value);
                      if (next) setCountry(next);
                    }}
                    aria-label="Country"
                  >
                    {COUNTRIES.map((item) => (
                      <option value={item.label} key={item.label}>
                        {item.label} {item.dialCode}
                      </option>
                    ))}
                  </select>
                  <input
                    className={styles.vgDemoInput}
                    value={phoneInput}
                    onChange={(event) => setPhoneInput(event.target.value)}
                    autoComplete="tel"
                    inputMode="tel"
                    placeholder="555 123 4567"
                  />
                </div>
              </label>

              <p className={styles.vgDemoStatus} aria-live="polite">
                {message}
              </p>

              <div className={styles.vgDemoActions}>
                <button className={`btn accent ${styles.vgDemoButton}`} type="submit" disabled={busy}>
                  {busy ? 'mocking...' : 'run the mock'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
