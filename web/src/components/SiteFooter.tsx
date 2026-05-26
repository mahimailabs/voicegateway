import styles from './SiteFooter.module.css';

const footerLinks = [
  { label: 'github', href: 'https://github.com/mahimailabs/voicegateway' },
  { label: 'linkedin', href: 'https://www.linkedin.com/in/mahimairaja/' },
  { label: 'changelog', href: '/docs/changelog' },
];

export default function SiteFooter() {
  return (
    <footer className={styles.vgFooter}>
      <div className={styles.vgFooterInner}>
        <nav className={styles.vgFooterNav} aria-label="Footer">
          {footerLinks.map((item, i) => (
            <span key={item.label}>
              <a href={item.href}>{item.label}</a>
              {i < footerLinks.length - 1 ? (
                <span className={styles.vgSep} aria-hidden="true">·</span>
              ) : null}
            </span>
          ))}
        </nav>
      </div>
    </footer>
  );
}
