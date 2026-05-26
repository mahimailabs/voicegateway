import styles from './WindowFrame.module.css';

interface Props {
  url?: string;
  children: React.ReactNode;
}

export default function WindowFrame({
  url = 'voicegateway.mahimai.ca',
  children,
}: Props) {
  return (
    <div className={styles.vgFrameOuter}>
      <div className={styles.vgFrame}>
        <div className={styles.vgFrameChrome} aria-hidden="true">
          <span className={styles.vgFrameDots}>
            <span className={styles.vgFrameDot} data-color="red" />
            <span className={styles.vgFrameDot} data-color="yellow" />
            <span className={styles.vgFrameDot} data-color="green" />
          </span>
          <span className={styles.vgFrameUrl}>{url}</span>
        </div>
        <div className={styles.vgFrameBody}>{children}</div>
      </div>
    </div>
  );
}
