'use client';

import { useMemo, type ReactNode } from 'react';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from 'fumadocs-ui/components/ui/popover';

type DocsAiActionsProps = {
  githubUrl: string;
  pageUrl: string;
};

type OpenItem = {
  href: string;
  icon: ReactNode;
  title: string;
};

export function DocsAiActions({ githubUrl, pageUrl }: DocsAiActionsProps) {
  const openItems = useMemo<OpenItem[]>(() => {
    const q = `Read ${pageUrl}, I want to ask questions about it.`;

    return [
      {
        title: 'Open in GitHub',
        href: githubUrl,
        icon: <GitHubIcon />,
      },
      {
        title: 'Open in Scira AI',
        href: `https://scira.ai/?${new URLSearchParams({ q })}`,
        icon: <SparkIcon />,
      },
      {
        title: 'Open in ChatGPT',
        href: `https://chatgpt.com/?${new URLSearchParams({
          hints: 'search',
          q,
        })}`,
        icon: <OpenAIIcon />,
      },
      {
        title: 'Open in Claude',
        href: `https://claude.ai/new?${new URLSearchParams({ q })}`,
        icon: <ClaudeIcon />,
      },
      {
        title: 'Open in Cursor',
        href: `https://cursor.com/link/prompt?${new URLSearchParams({ text: q })}`,
        icon: <CursorIcon />,
      },
    ];
  }, [githubUrl, pageUrl]);

  return (
    <div className="mb-8">
      <Popover>
        <PopoverTrigger
          className={`${buttonVariants({
            color: 'secondary',
            size: 'sm',
          })} gap-2 data-[state=open]:bg-fd-accent data-[state=open]:text-fd-accent-foreground`}
        >
          Open
          <ChevronDownIcon />
        </PopoverTrigger>
        <PopoverContent className="flex min-w-56 flex-col">
          {openItems.map((item) => (
            <a
              className="inline-flex items-center gap-2 rounded-lg p-2 text-sm hover:bg-fd-accent hover:text-fd-accent-foreground [&_svg]:size-4"
              href={item.href}
              key={item.title}
              rel="noreferrer noopener"
              target="_blank"
            >
              {item.icon}
              {item.title}
              <ExternalLinkIcon className="ms-auto size-3.5 text-fd-muted-foreground" />
            </a>
          ))}
        </PopoverContent>
      </Popover>
    </div>
  );
}

function ChevronDownIcon() {
  return (
    <svg aria-hidden="true" className="size-3.5 text-fd-muted-foreground" viewBox="0 0 20 20">
      <path
        d="m5 7 5 5 5-5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function ExternalLinkIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" className={className} fill="none" viewBox="0 0 24 24">
      <path
        d="M14 4h6v6M10 14 20 4M20 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
      <path d="M12 2C6.48 2 2 6.48 2 12c0 4.42 2.87 8.17 6.84 9.49.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.35-3.37-1.35-.45-1.16-1.11-1.46-1.11-1.46-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.64-1.34-2.22-.25-4.56-1.11-4.56-4.94 0-1.09.39-1.99 1.03-2.69-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.03A9.57 9.57 0 0 1 12 6.83c.85 0 1.71.11 2.5.33 1.91-1.3 2.75-1.03 2.75-1.03.55 1.38.2 2.4.1 2.65.64.7 1.03 1.6 1.03 2.69 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85V21c0 .27.18.58.69.48A10 10 0 0 0 22 12c0-5.52-4.48-10-10-10Z" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
      <path
        d="M12 2 9.6 8.4 3 11l6.6 2.6L12 20l2.4-6.4L21 11l-6.6-2.6L12 2Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function OpenAIIcon() {
  return (
    <svg aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
      <path d="M22.3 9.82a6 6 0 0 0-.52-4.91 6.05 6.05 0 0 0-6.5-2.9A6.06 6.06 0 0 0 5 4.18a6 6 0 0 0-4 2.9 6.05 6.05 0 0 0 .74 7.1 6 6 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9A6 6 0 0 0 13.26 24a6.06 6.06 0 0 0 5.77-4.2 6 6 0 0 0 4-2.9 6.06 6.06 0 0 0-.73-7.08Zm-9.04 12.61a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.8.8 0 0 0 .4-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.5 4.5Zm-9.66-4.13a4.47 4.47 0 0 1-.54-3.01l.15.08 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.33a.08.08 0 0 1-.03.06l-4.84 2.8A4.5 4.5 0 0 1 3.6 18.3ZM2.34 7.9A4.49 4.49 0 0 1 4.7 5.92v5.68a.77.77 0 0 0 .39.68l5.81 3.35-2.02 1.17a.08.08 0 0 1-.07 0l-4.83-2.79A4.5 4.5 0 0 1 2.34 7.9Zm16.6 3.85-5.84-3.39 2.02-1.16a.08.08 0 0 1 .07 0l4.83 2.79a4.49 4.49 0 0 1-.68 8.1v-5.67a.79.79 0 0 0-.4-.67Zm2.01-3.02-.14-.09-4.77-2.78a.78.78 0 0 0-.79 0L9.41 9.23V6.9a.07.07 0 0 1 .03-.06l4.83-2.79a4.5 4.5 0 0 1 6.68 4.68ZM8.31 12.86l-2.02-1.16a.08.08 0 0 1-.04-.06V6.07a4.5 4.5 0 0 1 7.38-3.45l-.14.08L8.7 5.46a.79.79 0 0 0-.39.68v6.72Zm1.1-2.36 2.6-1.5 2.6 1.5v3l-2.6 1.5-2.6-1.5v-3Z" />
    </svg>
  );
}

function ClaudeIcon() {
  return (
    <svg aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
      <path d="M17.3 3.54h-3.67l6.7 16.92H24L17.3 3.54ZM6.7 3.54 0 20.46h3.74l1.37-3.55h7.01l1.37 3.55h3.74L10.54 3.54H6.7Zm-.37 10.22 2.29-5.94 2.29 5.94H6.33Z" />
    </svg>
  );
}

function CursorIcon() {
  return (
    <svg aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
      <path d="M11.5.13 1.89 5.68a.84.84 0 0 0-.42.73v11.18c0 .3.16.58.42.73l9.61 5.55a1 1 0 0 0 1 0l9.61-5.55a.84.84 0 0 0 .42-.73V6.41a.84.84 0 0 0-.42-.73L12.5.13a1.01 1.01 0 0 0-1 0ZM2.66 6.34h18.55c.26 0 .43.29.3.51l-9.28 16.07c-.06.1-.23.06-.23-.06V12.34a.59.59 0 0 0-.3-.51L2.6 6.57c-.11-.06-.06-.23.06-.23Z" />
    </svg>
  );
}
