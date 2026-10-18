import { ImageZoom } from 'fumadocs-ui/components/image-zoom';

// One image per theme: public/<src>-light.<ext> and public/<src>-dark.<ext>.
export function Picture({ src, alt, ext = 'svg' }: { src: string; alt: string; ext?: string }) {
  return (
    <>
      <ImageZoom src={`${src}-light.${ext}`} alt={alt} width={1200} height={600} className="dark:hidden" />
      <ImageZoom src={`${src}-dark.${ext}`} alt={alt} width={1200} height={600} className="hidden dark:block" />
    </>
  );
}
