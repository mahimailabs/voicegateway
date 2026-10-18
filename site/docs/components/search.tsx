'use client';
import {
  SearchDialog,
  SearchDialogClose,
  SearchDialogContent,
  SearchDialogHeader,
  SearchDialogIcon,
  SearchDialogInput,
  SearchDialogList,
  SearchDialogOverlay,
  type SharedProps,
} from 'fumadocs-ui/components/dialog/search';
import { useDocsSearch } from 'fumadocs-core/search/client';
import { staticClient } from 'fumadocs-core/search/client/orama-static';
import { useI18n } from 'fumadocs-ui/contexts/i18n';
import { useEffect } from 'react';
import { track } from '@/lib/analytics';

export default function DefaultSearchDialog(props: SharedProps) {
  const { locale } = useI18n(); // (optional) for i18n
  const { search, setSearch, query } = useDocsSearch({
    client: staticClient({
      locale,
    }),
  });

  // One event per query someone settles on, with how many pages matched and the first
  // one: searches with no results, or the wrong top page, are what the docs are missing.
  const settled = search.trim();
  // Orama matches any one term, so the count runs high; the top page shows what the query found.
  const pages = query.isLoading || query.data === 'empty' || !query.data ? null : query.data.filter((item) => item.type === 'page');
  const results = pages?.length ?? null;
  const top = pages?.[0]?.url ?? '';
  useEffect(() => {
    if (settled.length < 2 || results === null) return;
    const timer = setTimeout(() => void track('docs_search', { query: settled.slice(0, 100), results, top }), 1500);
    return () => clearTimeout(timer);
  }, [settled, results, top]);

  return (
    <SearchDialog search={search} onSearchChange={setSearch} isLoading={query.isLoading} {...props}>
      <SearchDialogOverlay />
      <SearchDialogContent>
        <SearchDialogHeader>
          <SearchDialogIcon />
          <SearchDialogInput />
          <SearchDialogClose />
        </SearchDialogHeader>
        <SearchDialogList items={query.data !== 'empty' ? query.data : null} />
      </SearchDialogContent>
    </SearchDialog>
  );
}
