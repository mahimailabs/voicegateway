import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    '.next/**',
    'out/**',
    'build/**',
    'dist/**',
    'next-env.d.ts',
    '.open-next/**',
    '.source/**',
    '.vercel/**',
    '.astro/**',
    '.wrangler/**',
    'legacy/**',
    'worker-configuration.d.ts',
    'node_modules/**',
  ]),
]);

export default eslintConfig;
