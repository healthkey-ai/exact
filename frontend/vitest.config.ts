import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dedicated config so the unit run doesn't load the module-federation plugin
// from `vite.config.ts` (federation setup is irrelevant to pure-logic tests).
// `react()` is only here to transform JSX in imported `.tsx` modules — the
// tests are pure-logic and don't render, so `environment: node` is enough.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
