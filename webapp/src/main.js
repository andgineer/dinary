import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { runLegacyPwaCleanup } from "./legacy-pwa-cleanup.js";
import "./assets/base.css";

// Run the legacy-PWA sweep BEFORE Vue mounts (and therefore before
// vite-plugin-pwa's auto-injected ``register-service-worker`` script
// runs on window-load), so we don't accidentally unregister the new
// Workbox SW we just registered. The sweep is a one-shot guarded by
// a localStorage flag, so on subsequent loads this is a no-op.
//
// Wrapped in an async IIFE rather than using top-level ``await`` to
// stay within Vite's default browser target (es2020 / Safari 14),
// which doesn't support top-level await.
(async () => {
  await runLegacyPwaCleanup();
  const app = createApp(App);
  app.use(createPinia());
  app.mount("#app");
})();
