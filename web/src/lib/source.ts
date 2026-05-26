import { loader } from 'fumadocs-core/source';
import type { Node, Root } from 'fumadocs-core/page-tree';
import { docs } from '../../.source/server';

export const source = loader({
  baseUrl: '/docs',
  pageTree: {
    transformers: [
      {
        root(root) {
          return flattenTopLevelSections(root);
        },
      },
    ],
  },
  source: docs.toFumadocsSource(),
});

function flattenTopLevelSections(root: Root): Root {
  const children: Node[] = [];

  for (let i = 0; i < root.children.length; i += 1) {
    const node = root.children[i];
    const next = root.children[i + 1];

    if (node.type === 'separator' && next?.type === 'folder') {
      children.push(node, ...next.children);
      i += 1;
      continue;
    }

    children.push(node);
  }

  return { ...root, children };
}
