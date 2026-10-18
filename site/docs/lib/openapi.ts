import { createOpenAPI } from 'fumadocs-openapi/server';

// The contract filtered to what main serves, written by scripts/generate-api.mjs.
export const openapi = createOpenAPI({
  input: ['./openapi.served.json'],
});
