import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CodeBlock, Pre } from 'fumadocs-ui/components/codeblock';
import { describe, expect, it } from 'vitest';
import { buttonEvent } from '../lib/analytics';

// Renders fumadocs-ui's own code block, so a change to its copy button markup fails
// here instead of silently stopping code_copy events.
describe('code copy button', () => {
  it('is a button inside a figure whose text names the copy action', () => {
    const html = renderToStaticMarkup(createElement(CodeBlock, null, createElement(Pre, null, 'make up')));
    const figure = html.match(/<figure[\s\S]*<\/figure>/)?.[0] ?? '';
    const button = figure.match(/<button[\s\S]*?<\/button>/)?.[0] ?? '';
    const text = button.replace(/<[^>]+>/g, '');
    expect(text).toBe('Copy Text');
    expect(buttonEvent(text, true)).toBe('code_copy');
    expect(buttonEvent('Copied Text', true)).toBe('code_copy');
  });

  it('names the Markdown page action and ignores other buttons', () => {
    expect(buttonEvent(' Copy Markdown ', false)).toBe('markdown_copy');
    expect(buttonEvent('Copy Text', false)).toBeNull();
    expect(buttonEvent('Good', false)).toBeNull();
  });
});
