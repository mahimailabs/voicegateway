import styles from './PromiseCard.module.css';

interface Props {
  num: string;
  title: string;
  body: string;
}

export default function PromiseCard({ num, title, body }: Props) {
  return (
    <div className={`box soft ${styles.vgPromise}`}>
      <div className="tiny-mono">{num}</div>
      <div className={`h-hand ${styles.vgPromiseTitle}`}>{title}</div>
      <p className={`p-hand sm ${styles.vgPromiseBody}`}>{body}</p>
    </div>
  );
}
