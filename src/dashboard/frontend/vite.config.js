import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        // /api → dashboard backend (port 9090, the FastAPI app at
        // dashboard/api/main.py). /v1 → core HTTP API (port 8080, the
        // FastAPI app at voicegateway/server.py). The dev workflow
        // expects `voicegw serve` and `voicegw dashboard` running side
        // by side; production via combined_server.py mounts both on a
        // single port so the proxy is unnecessary there.
        proxy: {
            '/api': 'http://localhost:9090',
            '/v1': 'http://localhost:8080',
        },
    },
    build: {
        outDir: 'dist',
    },
});
