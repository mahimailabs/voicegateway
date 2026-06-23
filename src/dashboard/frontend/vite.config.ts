import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // After the dashboard fold-in, the daemon (`voicegw serve`) owns
    // both /v1/* (core API) and /api/* (dashboard API), so both proxy
    // targets point at port 8080. The previous split (/api -> :9090,
    // /v1 -> :8080) is gone along with the standalone dashboard
    // FastAPI app. In production the daemon also serves the built
    // bundle at /; the proxy below only matters for `npm run dev`.
    proxy: {
      '/api': 'http://localhost:8080',
      '/v1': 'http://localhost:8080',
      '/static': 'http://localhost:8080',
    },
  },
  build: {
    outDir: 'dist',
  },
});
