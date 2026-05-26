#!/usr/bin/env tsx
import { execFileSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readdirSync,
  statSync,
  readFileSync,
  writeFileSync,
  rmSync,
} from 'node:fs';
import { basename, join } from 'node:path';
import { tmpdir } from 'node:os';

const SOURCE_REPO = process.env.SOURCE_REPO ?? 'https://github.com/mahimailabs/voicegateway.git';
const SOURCE_REF = process.env.SOURCE_REF ?? 'main';
const TARGET = 'content/docs';
const NATIVE_DOCS = new Set(['index.md', 'index.mdx', 'get-started.md', 'get-started.mdx']);

const tmp = join(tmpdir(), `vg-docs-${Date.now()}`);

console.log(`Cloning ${SOURCE_REPO}@${SOURCE_REF} to ${tmp}`);
execFileSync('git', ['clone', '--depth', '1', '--branch', SOURCE_REF, SOURCE_REPO, tmp], {
  stdio: 'inherit',
});

const sourceDocs = join(tmp, 'docs');
if (!existsSync(sourceDocs)) {
  console.error(`SOURCE/docs/ not found at ${sourceDocs}`);
  rmSync(tmp, { recursive: true, force: true });
  process.exit(1);
}

// Clean previously-synced content before re-copying. Native files
// (index.md, get-started.md) are preserved; everything else is
// regenerated from source. Without this step, files or directories
// removed from the upstream docs/ tree would persist as stale local
// copies (the original symptom: docs/migration/ was deleted upstream
// but content/docs/migration/ stuck around in local checkouts).
if (existsSync(TARGET)) {
  for (const name of readdirSync(TARGET)) {
    if (NATIVE_DOCS.has(name)) continue;
    rmSync(join(TARGET, name), { recursive: true, force: true });
  }
}

function titleCase(slug: string) {
  return slug.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function ensureFrontmatter(content: string, filename: string): string {
  const fmMatch = content.match(/^---\n([\s\S]*?)\n---\n?/);
  let title: string | undefined;
  let description: string | undefined;
  let body = content;

  if (fmMatch) {
    const fm = fmMatch[1];
    const titleLine = fm.match(/^title:\s*(.+)$/m);
    const descLine = fm.match(/^description:\s*(.+)$/m);
    if (titleLine) title = titleLine[1].replace(/^["']|["']$/g, '').trim();
    if (descLine) description = descLine[1].replace(/^["']|["']$/g, '').trim();
    body = content.slice(fmMatch[0].length);
  }

  if (!title) {
    const h1 = body.match(/^#\s+(.+)$/m);
    if (h1) title = h1[1].trim();
    else title = titleCase(basename(filename).replace(/\.(md|mdx)$/, ''));
  }

  if (!description) {
    const para = body.match(/^(?!#|---)(.+?)(?:\n\n|\n*$)/m);
    if (para) {
      description = para[1].replace(/[\r\n]+/g, ' ').trim().slice(0, 200);
    }
  }

  const fmLines = [`title: ${JSON.stringify(title)}`];
  if (description) fmLines.push(`description: ${JSON.stringify(description)}`);

  return `---\n${fmLines.join('\n')}\n---\n\n${body.trimStart()}`;
}

function copyTree(src: string, dst: string, relativeDir = '') {
  if (!existsSync(dst)) mkdirSync(dst, { recursive: true });
  for (const name of readdirSync(src)) {
    const relativePath = relativeDir ? `${relativeDir}/${name}` : name;
    const s = join(src, name);
    const d = join(dst, name);
    if (statSync(s).isDirectory()) copyTree(s, d, relativePath);
    else if (name.endsWith('.md') || name.endsWith('.mdx')) {
      if (NATIVE_DOCS.has(relativePath)) continue;

      const raw = readFileSync(s, 'utf8');
      const containsKnownJsx =
        /<(DemoWidget|PackageManagerTabs|Mermaid|Files|Folder|File|Card|Cards|Tabs|Tab|Accordion|Accordions|Callout|Steps|Step)[\s/>]/.test(
          raw,
        );
      const targetName =
        name.endsWith('.md') && containsKnownJsx ? name.replace(/\.md$/, '.mdx') : name;
      const d = join(dst, targetName);
      if (targetName !== name) rmSync(join(dst, name), { force: true });
      writeFileSync(d, ensureFrontmatter(raw, name));
    }
  }
}
copyTree(sourceDocs, TARGET);

const changelog = join(tmp, 'CHANGELOG.md');
if (existsSync(changelog)) {
  const raw = readFileSync(changelog, 'utf8');
  const mdx = `---\ntitle: "Changelog"\ndescription: "VoiceGateway SDK release notes."\n---\n\n${raw}`;
  writeFileSync(join(TARGET, 'changelog.mdx'), mdx);
}

rmSync(tmp, { recursive: true, force: true });
console.log(`Synced docs from ${SOURCE_REPO}@${SOURCE_REF} into ${TARGET}`);
