import Image from 'next/image';
import Link from 'next/link';
import styles from './Nav.module.css';

const navItems = [
  { label: 'docs', href: '/docs' },
  { label: 'about', href: '/about' },
  { label: 'github', href: 'https://github.com/mahimailabs/voicegateway' },
  { label: 'changelog', href: '/docs/changelog' },
];

export default function Nav() {
  return (
    <header className={styles.vgTopbar}>
      <Link className={styles.vgWordmark} href="/" aria-label="VoiceGateway home">
        <Image className={styles.vgMascot} src="/brand/goat.svg" alt="" width={32} height={32} priority />
        <span className={styles.vgName}>VoiceGateway</span>
        <span className={styles.vgTag} aria-hidden="true">{'// SDK'}</span>
      </Link>

      <nav className={styles.vgLinks} aria-label="Primary">
        {navItems.map((item, i) => (
          <span key={item.label}>
            <a href={item.href}>{item.label}</a>
            {i < navItems.length - 1 ? (
              <span className={styles.vgSep} aria-hidden="true">·</span>
            ) : null}
          </span>
        ))}
      </nav>

      <Link className={`btn accent ${styles.vgCta}`} href="/docs/getting-started">
        try the SDK
      </Link>
    </header>
  );
}
