import { llms } from 'fumadocs-core/source/llms';
import { getCanonicalUrl } from '../../lib/docs-ai';
import { source } from '../../lib/source';

export const dynamic = 'force-static';

export function GET() {
  const index = llms(source).index().replaceAll('](/docs', `](${getCanonicalUrl('/docs')}`);

  return new Response(
    [
      '# VoiceGateway documentation',
      '',
      '> Public documentation for VoiceGateway, including setup, APIs, configuration, examples, architecture, and migration guides.',
      '',
      `- [Full Markdown corpus](${getCanonicalUrl('/llms-full.txt')})`,
      '',
      index.replace(/^# .+\n*/, ''),
    ].join('\n'),
    {
      headers: {
        'content-type': 'text/markdown; charset=utf-8',
      },
    },
  );
}
