import path from "node:path"
import { sentryVitePlugin } from "@sentry/vite-plugin"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// With a Sentry auth token in the environment (SENTRY_ORG and SENTRY_PROJECT name where), the build
// writes source maps, uploads them to Sentry and deletes them, so stack traces read as source but no
// map ships. Without one the build is unchanged: no maps, no upload (decision 0039).
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN
// Without an org and project the plugin skips the upload but still deletes the maps, so the build
// would pass with nothing uploaded.
if (sentryAuthToken && !(process.env.SENTRY_ORG && process.env.SENTRY_PROJECT)) {
  throw new Error("SENTRY_AUTH_TOKEN is set, so SENTRY_ORG and SENTRY_PROJECT must be set too")
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    ...(sentryAuthToken
      ? [sentryVitePlugin({ authToken: sentryAuthToken, telemetry: false, sourcemaps: { filesToDeleteAfterUpload: ["./dist/**/*.map"] } })]
      : []),
  ],
  build: { sourcemap: sentryAuthToken ? "hidden" : false },
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  // In development the console reaches web through this proxy, so its sign-in and API calls are
  // same-origin, as in production where web serves the console (decisions 0011 and 0027).
  server: {
    port: 5180,
    strictPort: true,
    // The API reference page imports the contract from docs/api, outside the dashboard.
    fs: { allow: [".", path.resolve(__dirname, "../docs/api")] },
    // changeOrigin stays false, so web sees the browser's Host (localhost:5180) and treats the
    // dev console as local; xfwd stays off, so no forwarding header marks it as proxied.
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET ?? "http://localhost:8080", changeOrigin: false },
      "/auth": { target: process.env.VITE_API_TARGET ?? "http://localhost:8080", changeOrigin: false },
    },
  },
})
