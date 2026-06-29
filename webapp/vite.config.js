import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

function readBuildVersion() {
  try {
    return execSync("git describe --exact-match --tags HEAD", {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim()
      .replace(/^v/, "");
  } catch {
    try {
      return execSync("git rev-parse --short HEAD", {
        stdio: ["ignore", "pipe", "ignore"],
      })
        .toString()
        .trim();
    } catch {
      return "dev";
    }
  }
}

const APP_VERSION = readBuildVersion();

// Workbox PWA strategy:
//
// - registerType 'autoUpdate' + skipWaiting + clientsClaim: the new
//   bundle is served on the very next reload after deploy.
// - globPatterns precaches every hashed Vite output, so the PWA can
//   boot fully offline once it has been opened online once.
// - navigateFallback to index.html keeps SPA navigations working
//   offline, excluding /api/* so backend calls always go to network.
// - runtimeCaching adds a NetworkOnly policy for /api/* as a belt-
//   and-braces guarantee that no API response is ever cached.
export default defineConfig({
  resolve: {
    alias: {
      // Point directly at the browser-inlined build (WASM as base64, no CDN,
      // no separate .wasm fetch) instead of letting Vite pick via export
      // conditions, which resolves to the Node.js entry and emits a warning.
      "@undecaf/zbar-wasm": fileURLToPath(
        new URL(
          "node_modules/@undecaf/zbar-wasm/dist/inlined/index.mjs",
          import.meta.url,
        ),
      ),
    },
  },
  plugins: [
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",
      manifest: false,
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico,webmanifest,json}"],
        skipWaiting: true,
        clientsClaim: true,
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  build: {
    outDir: "../_static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
