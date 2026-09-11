import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dedicated config so the unit run doesn't load the module-federation plugin
// from `vite.config.ts` (federation setup is irrelevant to these tests).
//
// Two projects, because the two kinds of test want different environments and
// the cheap one should stay cheap. The pure-logic suite runs in `node` and
// finishes in milliseconds; only the component suite pays for jsdom.
//
// The component suite exists because three review rounds in a row found bugs
// that no pure-logic test could reach — a page-reset effect that could never
// fire, a stale-data indicator keyed on the wrong flag, a debounce that fired
// an extra request for the previous filter (#426). Those live in the wiring
// between state, effects and the query client, and the only way to test that
// wiring is to render it.
export default defineConfig({
  plugins: [react()],
  test: {
    projects: [
      {
        plugins: [react()],
        test: {
          name: "logic",
          environment: "node",
          include: ["src/**/*.test.ts"],
        },
      },
      {
        plugins: [react()],
        test: {
          name: "component",
          environment: "jsdom",
          include: ["src/**/*.test.tsx"],
          setupFiles: ["./src/test/setup.ts"],
        },
      },
    ],
  },
});
