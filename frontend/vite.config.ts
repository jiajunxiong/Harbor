import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * The dashboard is a pure consumer of the read-only Harbor API (MVP 5 / SP 5.3):
 * there is no dev-server middleware that could mutate state, and `/api` is
 * proxied to the API process so development uses the very same request path as
 * production (SP 5.9).
 */
const apiTarget = process.env.HARBOR_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/health": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    // The ECharts vendor chunk is ~500 kB by nature; the *application* shell is
    // budgeted at 60 kB, which is the number a regression would move (SP 5.70).
    chunkSizeWarningLimit: 600,
    // ECharts is the bulk of the bundle and is needed only by the chart cards,
    // so it is split out: the audit tables stay readable on a slow connection
    // even if the chart chunk is still in flight (SP 5.70).
    rollupOptions: {
      output: {
        manualChunks: (id: string) => {
          if (id.includes("node_modules/echarts") || id.includes("node_modules/zrender")) {
            return "echarts";
          }
          if (
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules/react/") ||
            id.includes("node_modules/scheduler")
          ) {
            return "react";
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    unstubGlobals: true,
  },
});
