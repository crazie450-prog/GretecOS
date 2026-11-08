// The dashboard build (WO-2.6). `pnpm build` writes dist/, which the core serves at / (D159).
//
// `pnpm dev` is for gos-dev's own core only (D115, D117): it serves on localhost:5173 and proxies
// /api and /ws to GOS_CORE_URL (default gos-dev's http://127.0.0.1:8775), presenting itself as
// GOS_DASHBOARD_HOST (default gos-dev.localhost) so the core's Host and Origin checks and pairing
// accept it. Never point it at Clayton's core or serve it on gos.localhost.
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import type { ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";
import { gosDesign } from "./design/plugin.ts";
import { REPO_ROOT } from "./design/designSystem.ts";

const root = fileURLToPath(new URL(".", import.meta.url));
const core = new URL(process.env.GOS_CORE_URL ?? "http://127.0.0.1:8775");
const presented = `${process.env.GOS_DASHBOARD_HOST ?? "gos-dev.localhost"}:${core.port || 80}`;

const asDashboard: ProxyOptions["configure"] = (proxy) => {
  proxy.on("proxyReq", (req) => {
    req.setHeader("host", presented);
    req.setHeader("origin", `http://${presented}`);
  });
  proxy.on("proxyReqWs", (req) => {
    req.setHeader("host", presented);
    req.setHeader("origin", `http://${presented}`);
  });
};

export default defineConfig({
  plugins: [react(), gosDesign(root)],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    assetsInlineLimit: 0, // fonts as files: the page's CSP allows fonts from 'self' only
    modulePreload: { polyfill: false },
    chunkSizeWarningLimit: 800,
  },
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    fs: { allow: [REPO_ROOT] },
    proxy: {
      "/api": { target: core.origin, configure: asDashboard },
      "/ws": { target: core.origin, ws: true, configure: asDashboard },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
