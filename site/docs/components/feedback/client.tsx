'use client';
// Vendored from fumadocs (apps/docs/components/feedback/client.tsx at
// fuma-nama/fumadocs@7b2b93d), the component `npx @fumadocs/cli add feedback`
// installs. Only the page feedback is kept. Local edits: the question reads "Was
// this page helpful?", the message is optional so a thumbs up or down alone is
// sent, the GitHub link shows only when the action returns one, and localStorage
// access is guarded for private modes that block it.
import { cn } from '@/lib/cn';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { type SyntheticEvent, useEffect, useEffectEvent, useState, useTransition } from 'react';
import { Collapsible, CollapsibleContent } from 'fumadocs-ui/components/ui/collapsible';
import { cva } from 'class-variance-authority';
import { actionResponse, pageFeedback, type ActionResponse, type PageFeedback } from './schema';
import { z } from 'zod/mini';
import { usePathname } from 'fumadocs-core/framework';

const rateButtonVariants = cva(
  'inline-flex items-center gap-2 px-3 py-2 rounded-full font-medium border text-sm [&_svg]:size-4 disabled:cursor-not-allowed',
  {
    variants: {
      active: {
        true: 'bg-fd-accent text-fd-accent-foreground [&_svg]:fill-current',
        false: 'text-fd-muted-foreground',
      },
    },
  },
);

const pageFeedbackResult = z.extend(pageFeedback, {
  response: actionResponse,
});

/**
 * A feedback component to be attached at the end of page
 */
export function Feedback({
  onSendAction,
}: {
  onSendAction: (feedback: PageFeedback) => Promise<ActionResponse>;
}) {
  const pathname = usePathname();
  const { previous, setPrevious } = useSubmissionStorage(pathname, (v) => {
    const result = pageFeedbackResult.safeParse(v);
    return result.success ? result.data : null;
  });
  const [opinion, setOpinion] = useState<'good' | 'bad' | null>(null);
  const [message, setMessage] = useState('');
  const [isPending, startTransition] = useTransition();

  function submit(e?: SyntheticEvent) {
    if (opinion == null) return;

    startTransition(async () => {
      const feedback: PageFeedback = {
        url: location.href,
        opinion,
        message,
      };

      const response = await onSendAction(feedback);
      setPrevious({
        response,
        ...feedback,
      });
      setMessage('');
      setOpinion(null);
    });

    e?.preventDefault();
  }

  const activeOpinion = previous?.opinion ?? opinion;

  return (
    <Collapsible
      open={opinion !== null || previous !== null}
      onOpenChange={(v) => {
        if (!v) setOpinion(null);
      }}
      className="border-y py-3"
    >
      <div className="flex flex-row items-center gap-2">
        <p className="text-sm font-medium pe-2">Was this page helpful?</p>
        <button
          disabled={previous !== null}
          className={cn(
            rateButtonVariants({
              active: activeOpinion === 'good',
            }),
          )}
          onClick={() => {
            setOpinion('good');
          }}
        >
          <ThumbsUp />
          Good
        </button>
        <button
          disabled={previous !== null}
          className={cn(
            rateButtonVariants({
              active: activeOpinion === 'bad',
            }),
          )}
          onClick={() => {
            setOpinion('bad');
          }}
        >
          <ThumbsDown />
          Bad
        </button>
      </div>
      <CollapsibleContent className="mt-3">
        {previous ? (
          <div className="px-3 py-6 flex flex-col items-center gap-3 bg-fd-card text-fd-muted-foreground text-sm text-center rounded-xl">
            <p>Thank you for your feedback!</p>
            <div className="flex flex-row items-center gap-2">
              {previous.response?.githubUrl && <a
                href={previous.response?.githubUrl}
                rel="noreferrer noopener"
                target="_blank"
                className={cn(
                  buttonVariants({
                    variant: 'default',
                  }),
                  'text-xs',
                )}
              >
                View on GitHub
              </a>}

              <button
                className={cn(
                  buttonVariants({
                    variant: 'secondary',
                  }),
                  'text-xs',
                )}
                onClick={() => {
                  setOpinion(previous.opinion);
                  setPrevious(null);
                }}
              >
                Submit Again
              </button>
            </div>
          </div>
        ) : (
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <textarea
              autoFocus
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              className="border rounded-lg bg-fd-secondary text-fd-secondary-foreground p-3 resize-none focus-visible:outline-none placeholder:text-fd-muted-foreground"
              placeholder="Anything we should add or fix? (optional)"
              onKeyDown={(e) => {
                // keyCode 229: Safari fires `compositionend` before this keydown, `isComposing` is already false
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                if (!e.shiftKey && e.key === 'Enter') {
                  submit(e);
                }
              }}
            />
            <button
              type="submit"
              className={cn(buttonVariants({ variant: 'outline' }), 'w-fit px-3')}
              disabled={isPending}
            >
              Submit
            </button>
          </form>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

function useSubmissionStorage<Result>(key: string, validate: (v: unknown) => Result | null) {
  const storageKey = `docs-feedback-${key}`;
  const [value, setValue] = useState<Result | null>(null);
  const validateCallback = useEffectEvent(validate);

  useEffect(() => {
    try {
      const item = localStorage.getItem(storageKey);
      if (item === null) return;
      const validated = validateCallback(JSON.parse(item));

      if (validated !== null) setValue(validated);
    } catch {
      // Storage blocked (some private modes) or a corrupt entry: show the form.
    }
  }, [storageKey]);

  return {
    previous: value,
    setPrevious(result: Result | null) {
      try {
        if (result) localStorage.setItem(storageKey, JSON.stringify(result));
        else localStorage.removeItem(storageKey);
      } catch {
        // Storage blocked: remember the answer for this page view only.
      }

      setValue(result);
    },
  };
}
