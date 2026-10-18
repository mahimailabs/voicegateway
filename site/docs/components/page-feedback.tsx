'use client';
import { Feedback } from '@/components/feedback/client';
import { track } from '@/lib/analytics';

// A static export has no server actions, so feedback goes to PostHog from the browser.
export function PageFeedback() {
  return (
    <Feedback
      onSendAction={async ({ opinion, message }) => {
        await track('docs_feedback', { opinion, message: message.trim().slice(0, 1000), page: location.pathname });
        return {};
      }}
    />
  );
}
