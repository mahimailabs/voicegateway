import { readFile } from 'node:fs/promises';
import { source } from '@/lib/source';
import { notFound } from 'next/navigation';
import { generateOGImage } from 'fumadocs-ui/og';
import { appName, getPageImageUrl } from '@/lib/shared';

export const revalidate = false;

export async function GET(_req: Request, { params }: RouteContext<'/og/docs/[...slug]'>) {
  const { slug } = await params;
  const page = source.getPage(slug.slice(0, -1));
  if (!page) notFound();

  // The console's mark for dark grounds; the image is drawn on Fumadocs' dark card.
  const mark = await readFile('public/brand/mark-dark.svg', 'base64');

  return generateOGImage({
    title: page.data.title,
    description: page.data.description,
    site: appName,
    icon: <img src={`data:image/svg+xml;base64,${mark}`} width={44} height={44} alt="" />,
    primaryColor: '#2997ff',
    primaryTextColor: '#2997ff',
  });
}

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    lang: page.locale,
    slug: getPageImageUrl(page).segments,
  }));
}
