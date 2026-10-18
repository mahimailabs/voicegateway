'use client';
import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { linkEvent, track, trackPageview } from '@/lib/analytics';

// Page views, plus the clicks that say a page helped: copying a code block or the
// page as Markdown, and leaving for GitHub, the console or an AI assistant.
export function Analytics() {
  const pathname = usePathname();
  useEffect(() => { void trackPageview(pathname); }, [pathname]);
  useEffect(() => {
    function click(event: MouseEvent) {
      if (!(event.target instanceof Element)) return;
      const page = location.pathname;
      const link = event.target.closest('a[href]');
      if (link instanceof HTMLAnchorElement) {
        const found = linkEvent(link.href, location.origin);
        if (found) void track(found.name, { destination: found.destination, page });
        return;
      }
      const button = event.target.closest('button');
      if (!button) return;
      // fumadocs-ui's code block copy button and its page action "Copy Markdown".
      if (button.closest('figure') && button.getAttribute('aria-label') === 'Copy Text') void track('code_copy', { page });
      else if (button.textContent?.trim() === 'Copy Markdown') void track('markdown_copy', { page });
    }
    document.addEventListener('click', click);
    return () => document.removeEventListener('click', click);
  }, []);
  return null;
}
