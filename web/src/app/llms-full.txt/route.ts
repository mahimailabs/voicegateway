import {
  getCanonicalUrl,
  getDocsPageMarkdown,
  uniqueDocsPages,
} from '../../lib/docs-ai';

export const dynamic = 'force-static';

export async function GET() {
  const sections = await Promise.all(
    uniqueDocsPages().map(async (page) => {
      const markdown = await getDocsPageMarkdown(page);

      return [
        `# ${page.data.title ?? page.url}`,
        '',
        `URL: ${getCanonicalUrl(page.url)}`,
        '',
        markdown.trim(),
      ].join('\n');
    }),
  );

  return new Response(
    `${[
      '# VoiceGateway documentation',
      '',
      'This file contains the processed Markdown corpus for the public VoiceGateway documentation.',
      '',
    ].join('\n')}${sections.length > 0 ? `\n\n---\n\n${sections.join('\n\n---\n\n')}` : ''}`,
    {
      headers: {
        'content-type': 'text/markdown; charset=utf-8',
      },
    },
  );
}
