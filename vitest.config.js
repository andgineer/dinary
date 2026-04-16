import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    setupFiles: ["allure-vitest/setup", "./tests/js/setup.js"],
    reporters: [
      "verbose",
      ["allure-vitest/reporter", { resultsDir: "./allure-results" }],
    ],
  },
});
