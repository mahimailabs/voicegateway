import { createOpenAPI } from 'fumadocs-openapi/server';

// The same contract the console renders at /docs.
export const openapi = createOpenAPI({
  input: ['../../docs/api/openapi.yaml'],
});
