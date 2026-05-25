import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import AllureReporter from "allure-vitest/reporter";

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify("test"),
  },
  test: {
    environment: "happy-dom",
    setupFiles: ["allure-vitest/setup", "./tests/setup.js"],
    reporters: ["verbose", new AllureReporter({ resultsDir: "../allure-results" })],
  },
});
