'use client';
import { Analytics } from '@/components/analytics';
import SearchDialog from '@/components/search';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { type ReactNode } from 'react';

export function Provider({ children }: { children: ReactNode }) {
  return (
    <RootProvider search={{ SearchDialog }} theme={{ defaultTheme: 'light', enableSystem: false }}>
      <Analytics />
      {children}
    </RootProvider>
  );
}
