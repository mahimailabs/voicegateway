import defaultMdxComponents from 'fumadocs-ui/mdx';
import { File, Files, Folder } from 'fumadocs-ui/components/files';
import type { MDXComponents } from 'mdx/types';
import DemoWidget, { type DemoWidgetProps } from './src/components/DemoWidget';
import { Mermaid } from './src/components/Mermaid';
import { PackageManagerTabs } from './src/components/PackageManagerTabs';

function DemoWidgetMdx(props: Partial<DemoWidgetProps>) {
  return (
    <DemoWidget
      placement={props.placement ?? 'docs'}
      title={props.title}
      body={props.body}
    />
  );
}

export function getMDXComponents(components?: MDXComponents): MDXComponents {
  return {
    ...defaultMdxComponents,
    DemoWidget: DemoWidgetMdx,
    File,
    Files,
    Folder,
    Mermaid,
    PackageManagerTabs,
    ...components,
  };
}
