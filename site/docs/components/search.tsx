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

  // One event per query someone settles on, with how many pages matched: searches
  // with no results are the pages the docs are missing.
  const settled = search.trim();
  const results = query.isLoading || query.data === 'empty' || !query.data ? null : query.data.filter((item) => item.type === 'page').length;
  useEffect(() => {
    if (settled.length < 2 || results === null) return;
    const timer = setTimeout(() => void track('docs_search', { query: settled.slice(0, 100), results }), 1500);
    return () => clearTimeout(timer);
  }, [settled, results]);

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
