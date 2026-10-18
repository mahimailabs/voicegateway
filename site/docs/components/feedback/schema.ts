// Vendored from fumadocs (apps/docs/components/feedback/schema.ts at fuma-nama/fumadocs@7b2b93d).
import { z } from 'zod/mini';

export const pageFeedback = z.object({
  opinion: z.enum(['good', 'bad']),
  /** full URL of page where fired */
  url: z.string(),
  message: z.string(),
});

export const actionResponse = z.object({
  githubUrl: z.optional(z.string()),
});

export type PageFeedback = z.infer<typeof pageFeedback>;
export type ActionResponse = z.infer<typeof actionResponse>;
