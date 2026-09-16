import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The compiled bundle is committed under src/memory_service_app/dashboard_dist
// so wheel/sdist builds are reproducible without a Node toolchain.
export default defineConfig({
  base: "/dashboard/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  build: {
    outDir: path.resolve(import.meta.dirname, "../../src/memory_service_app/dashboard_dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 58803,
    proxy: { "/dashboard/api": "http://127.0.0.1:58802" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
  },
})
