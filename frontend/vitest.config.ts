import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname),
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://127.0.0.1/" },
    },
    setupFiles: [resolve(import.meta.dirname, "src/test-setup.ts")],
    restoreMocks: true,
    clearMocks: true,
  },
});
