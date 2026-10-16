import { defineConfig } from "@hey-api/openapi-ts"

export default defineConfig({
  input: "../docs/api/openapi.yaml",
  output: { path: "src/lib/api-types", clean: true },
  plugins: ["@hey-api/typescript"],
})
