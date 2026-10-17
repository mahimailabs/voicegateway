import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { compile, splitLines, DARK_THEME, THEMES } from 'reladraw';
import { openSync } from 'fontkit';
import { DOMParser, XMLSerializer } from '@xmldom/xmldom';
import rough from 'roughjs/bundled/rough.cjs.js';

// Renders docs/assets/readme/components.reladraw into a light and a dark SVG for the README.
// Reladraw owns every position and edge route; Rough.js adds only the ink, and Kalam's outlines
// replace the text so GitHub needs no font. Icon files named in the source are inlined first,
// since only reladraw's command line reads files.
const dir = new URL('../../docs/assets/readme/', import.meta.url);
const font = openSync(fileURLToPath(new URL('./Kalam-Regular.ttf', import.meta.url)));
const fontSize = 20;
const lineHeight = size => size * 1.42;
const width = (text, size) => font.layout(text).advanceWidth * size / font.unitsPerEm;
const measurer = {
  fontFamily: 'Kalam',
  lineHeight,
  measure(text, size) {
    const lines = splitLines(text);
    return { lines, width: Math.max(0, ...lines.map(line => width(line, size))), height: lines.length * lineHeight(size) };
  },
};
const themes = {
  dark: { ...DARK_THEME, background: '#141414', text: '#f5f5f7', mutedText: '#aaaab0', boxFill: '#1c1c1f', boxStroke: '#68686e', edge: '#aaaab0', iconInk: '#f5f5f7', iconShade: '#2a2a2e' },
  light: { ...THEMES.light, background: '#ffffff', text: '#1d1d1f', mutedText: '#6e6e73', boxFill: '#fafafa', boxStroke: '#86868b', edge: '#6e6e73', iconInk: '#1d1d1f', iconShade: '#ececf0' },
};

let source = await readFile(new URL('components.reladraw', dir), 'utf8');
for (const [line, name, file] of [...source.matchAll(/^icon (\S+) "([^"]+)"$/gm)]) {
  source = source.replace(line, `icon ${name} """${(await readFile(new URL(file, dir), 'utf8')).trim()}"""`);
}

for (const [name, theme] of Object.entries(themes)) {
  const svg = compile(source, { fontSize, measurer, theme });
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
  const root = doc.documentElement;
  const ns = root.namespaceURI;
  const element = (tag, attributes) => {
    const node = doc.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  };
  const generator = rough.generator();
  let seed = 1;
  for (const path of Array.from(root.getElementsByTagName('path'))) {
    const stroke = path.getAttribute('stroke');
    if (!stroke || stroke === 'none' || path.hasAttribute('marker-start') || path.hasAttribute('marker-end')) continue;
    const drawable = generator.path(path.getAttribute('d'), {
      stroke, fill: path.getAttribute('fill') || undefined, fillStyle: 'solid',
      strokeWidth: 1.4, roughness: 0.65, bowing: 0.65, seed: seed++,
    });
    const group = element('g', {});
    for (const generated of generator.toPaths(drawable)) {
      const rendered = element('path', generated);
      for (const name of ['marker-start', 'marker-end', 'stroke-dasharray']) {
        if (path.hasAttribute(name)) rendered.setAttribute(name, path.getAttribute(name));
      }
      group.appendChild(rendered);
    }
    path.parentNode.replaceChild(group, path);
  }

  // Outline every label, so GitHub needs no external font and cannot substitute it.
  for (const text of Array.from(root.getElementsByTagName('text'))) {
    if (text.getElementsByTagName('tspan').length) throw new Error('Add explicit tspan support before using inline mixed-color labels.');
    const label = text.textContent;
    let inherited = text;
    while (inherited && !inherited.hasAttribute?.('font-size')) inherited = inherited.parentNode;
    const size = Number.parseFloat(inherited?.getAttribute('font-size') || String(fontSize));
    const run = font.layout(label);
    let x = Number(text.getAttribute('x'));
    const y = Number(text.getAttribute('y'));
    const scale = size / font.unitsPerEm;
    const anchor = text.getAttribute('text-anchor');
    if (anchor === 'middle') x -= run.advanceWidth * scale / 2;
    if (anchor === 'end') x -= run.advanceWidth * scale;
    const group = element('g', { fill: text.getAttribute('fill'), 'aria-label': label });
    const title = element('title', {});
    title.textContent = label;
    group.appendChild(title);
    let cursor = 0;
    for (let i = 0; i < run.glyphs.length; i++) {
      const position = run.positions[i];
      const path = element('path', {
        d: run.glyphs[i].path.toSVG(),
        transform: `translate(${x + (cursor + position.xOffset) * scale} ${y - position.yOffset * scale}) scale(${scale} ${-scale})`,
      });
      group.appendChild(path);
      cursor += position.xAdvance;
    }
    text.parentNode.replaceChild(group, text);
  }
  root.removeAttribute('font-family');
  root.removeAttribute('font-size');
  root.setAttribute('role', 'img');
  root.setAttribute('aria-labelledby', 'components-title components-description');
  const title = element('title', { id: 'components-title' });
  title.textContent = 'VoiceGateway components';
  const description = element('desc', { id: 'components-description' });
  description.textContent = 'LiveKit sends webhooks and metrics, and voice agents send spans and session uploads, to web. web checks keys, appends to a Redis stream and serves the API to the dashboard and MCP. worker consumes the stream and writes batches to ClickHouse. Postgres holds projects, keys and the call index; an S3 bucket holds opt-in recordings.';
  root.insertBefore(description, root.firstChild);
  root.insertBefore(title, root.firstChild);
  const fontLicense = element('metadata', { id: 'kalam-font-license' });
  fontLicense.textContent = await readFile(new URL('./Kalam-OFL.txt', import.meta.url), 'utf8');
  root.appendChild(fontLicense);
  const output = new XMLSerializer().serializeToString(doc) + '\n';
  // The repository caps every file at 250 KiB; fail the build rather than commit a larger SVG.
  const bytes = Buffer.byteLength(output);
  if (bytes > 250 * 1024) throw new Error(`components-${name}.svg is ${bytes} bytes, over the 250 KiB file cap.`);
  await writeFile(new URL(`components-${name}.svg`, dir), output);
  console.log(`Built components-${name}.svg (${root.getAttribute('width')} x ${root.getAttribute('height')})`);
}
