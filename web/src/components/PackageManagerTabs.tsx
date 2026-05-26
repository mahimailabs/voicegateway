'use client';
import { Tabs, Tab } from 'fumadocs-ui/components/tabs';
import { CopyButton } from './CopyButton';

interface Props {
  pkg: string;
}

function row(cmd: string) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <code style={{ flex: 1 }}>{cmd}</code>
      <CopyButton text={cmd} />
    </div>
  );
}

export function PackageManagerTabs({ pkg }: Props) {
  const uvCmd = `uv add ${pkg}`;
  const pipCmd = `pip install ${pkg}`;
  const poetryCmd = `poetry add ${pkg}`;

  return (
    <Tabs items={['uv', 'pip', 'poetry']} defaultIndex={0}>
      <Tab value="uv">{row(uvCmd)}</Tab>
      <Tab value="pip">{row(pipCmd)}</Tab>
      <Tab value="poetry">{row(poetryCmd)}</Tab>
    </Tabs>
  );
}
