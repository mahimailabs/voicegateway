import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  // In development the console reaches web through this proxy, so its sign-in and API calls are
  // same-origin, as in production where web serves the console (decisions 0011 and 0027).
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      "/api": process.env.VITE_API_TARGET ?? "http://localhost:8080",
      "/auth": process.env.VITE_API_TARGET ?? "http://localhost:8080",
    },
  },
})
