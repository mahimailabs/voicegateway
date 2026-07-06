import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Built to dist/ and served by `voicegw serve` under /console/, so assets
// resolve from that base. openorca-ui's styles.css is source Tailwind v4, which
// the @tailwindcss/vite plugin processes.
export default defineConfig({
  base: '/console/',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
});
