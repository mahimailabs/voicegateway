type MarkdownNode = {
  children?: MarkdownNode[];
  data?: Record<string, unknown>;
  lang?: string | null;
  meta?: string | null;
  name?: string;
  type: string;
  value?: string;
};

type ParentNode = MarkdownNode & {
  children: MarkdownNode[];
};

const languageTitles: Record<string, string> = {
  bash: 'Shell',
  sh: 'Shell',
  shell: 'Shell',
  zsh: 'Shell',
  python: 'Python',
  py: 'Python',
  yaml: 'YAML',
  yml: 'YAML',
  json: 'JSON',
  jsonc: 'JSON',
  ts: 'TypeScript',
  tsx: 'TSX',
  js: 'JavaScript',
  jsx: 'JSX',
  text: 'Text',
  txt: 'Text',
  toml: 'TOML',
  dockerfile: 'Dockerfile',
  csv: 'CSV',
  sql: 'SQL',
  mermaid: 'Mermaid',
  md: 'Markdown',
  markdown: 'Markdown',
  diff: 'Diff',
  env: '.env',
};

const codeGroupMarkerPattern = /^:::\s*(?:code-group)?\s*$/;
const legacyBracketMetaPattern = /(?:^|\s)\[([^\]\n]+)\](?=\s|$)/;

export function remarkLegacyCodeGroups() {
  return (tree: MarkdownNode) => {
    normalizeCodeGroupMarkers(tree);
    walk(tree, (node) => {
      if (node.type !== 'code' || !node.meta) return;

      const tabName = readLegacyBracketMeta(node.meta);
      if (!tabName || hasAttribute(node.meta, ['tab', 'title'])) return;

      node.meta = appendAttribute(
        removeLegacyBracketMeta(node.meta),
        'tab',
        tabName,
      );
    });
  };
}

export function remarkCodeBlockTitles() {
  return (tree: MarkdownNode) => {
    walk(tree, (node) => {
      if (node.type !== 'code') return;
      if (node.data?.tab) return;

      const meta = node.meta ?? '';
      if (hasAttribute(meta, ['title', 'tab'])) return;

      const explicitTitle = readAttribute(meta, ['file', 'filename', 'path']);
      const legacyTitle = readLegacyBracketMeta(meta);
      const title =
        explicitTitle ??
        legacyTitle ??
        inferCodeBlockTitle(node.lang, node.value ?? '');

      if (!title) return;

      const nextMeta = legacyTitle ? removeLegacyBracketMeta(meta) : meta;
      node.meta = appendAttribute(nextMeta, 'title', title);
    });
  };
}

function normalizeCodeGroupMarkers(node: MarkdownNode) {
  if (!node.children) return;

  const parent = node as ParentNode;
  for (let i = 0; i < parent.children.length; i += 1) {
    const child = parent.children[i];

    if (child.type === 'containerDirective' && child.name === 'code-group') {
      const replacement = child.children ?? [];
      parent.children.splice(i, 1, ...replacement);
      i += replacement.length - 1;
      continue;
    }

    if (isCodeGroupMarker(child)) {
      parent.children.splice(i, 1);
      i -= 1;
      continue;
    }

    normalizeCodeGroupMarkers(child);
  }
}

function isCodeGroupMarker(node: MarkdownNode) {
  if (typeof node.value === 'string' && codeGroupMarkerPattern.test(node.value.trim())) {
    return true;
  }

  if (node.type !== 'paragraph' || !node.children) return false;

  const value = node.children
    .map((child) => (typeof child.value === 'string' ? child.value : ''))
    .join('')
    .trim();

  return codeGroupMarkerPattern.test(value);
}

function walk(node: MarkdownNode, visitor: (node: MarkdownNode) => void) {
  visitor(node);

  for (const child of node.children ?? []) {
    walk(child, visitor);
  }
}

function hasAttribute(meta: string, names: string[]) {
  return names.some((name) =>
    new RegExp(`(?:^|\\s)${escapeRegex(name)}(?:=|\\s|$)`).test(meta),
  );
}

function readAttribute(meta: string, names: string[]) {
  for (const name of names) {
    const match = meta.match(
      new RegExp(
        `(?:^|\\s)${escapeRegex(name)}=(?:"([^"]*)"|'([^']*)'|([^\\s]+))`,
      ),
    );

    if (match) return match[1] ?? match[2] ?? match[3];
  }
}

function readLegacyBracketMeta(meta: string) {
  return meta.match(legacyBracketMetaPattern)?.[1]?.trim();
}

function removeLegacyBracketMeta(meta: string) {
  return meta.replace(legacyBracketMetaPattern, ' ').replace(/\s+/g, ' ').trim();
}

function appendAttribute(meta: string, name: string, value: string) {
  const attribute = `${name}="${value.replaceAll('"', "'")}"`;
  const trimmed = meta.trim();

  return trimmed ? `${trimmed} ${attribute}` : attribute;
}

function inferCodeBlockTitle(lang: string | null | undefined, code: string) {
  const language = lang?.toLowerCase();

  if (language === 'yaml' || language === 'yml') {
    if (/^services:\s*$/m.test(code)) return 'docker-compose.yml';
    if (
      /^(providers|projects|default_project|cost_tracking|observability|fallbacks|models):\s*$/m.test(
        code,
      )
    ) {
      return 'voicegw.yaml';
    }
  }

  if (language === 'python' || language === 'py') {
    if (/from voicegateway import inference|AgentSession/.test(code)) {
      return 'agent.py';
    }
  }

  if (!language) return 'Plain text';

  return languageTitles[language] ?? language.toUpperCase();
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
