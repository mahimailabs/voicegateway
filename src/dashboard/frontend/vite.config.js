import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// The demo build (`npm run build:demo`, i.e. `vite build --mode demo`) is served
// at voicegateway.dev/demo, so it needs a '/demo/' base and its own output dir so
// it never clobbers the real dashboard bundle. The normal build is unchanged.
// Driven off Vite's --mode (no process.env), which also sets import.meta.env.MODE
// that src/lib/demo.ts reads for the DEMO_MODE flag.
export default defineConfig(function (_a) {
    var mode = _a.mode;
    var isDemo = mode === 'demo';
    return {
        plugins: [react()],
        base: isDemo ? '/demo/' : '/',
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
                '/static/branding': { target: 'http://localhost:8080', changeOrigin: true },
            },
        },
        build: {
            outDir: isDemo ? 'dist-demo' : 'dist',
        },
    };
});
