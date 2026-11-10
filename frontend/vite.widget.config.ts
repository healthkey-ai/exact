import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Self-contained "widget" build of the TrialMatches surface — see
// src/federation/widget.tsx for WHY this exists alongside the Module-Federation
// remote (vite.remote.config.ts). The remote SHARES React as a singleton with
// its host; this build BUNDLES its own React 19 + QueryClient + axios so a host
// on a different React version (CB's React 18 `ui/`) can mount it in isolation.
//
// Output: dist/widget/exact-trials.js — one ES module exporting `mount`/`unmount`.
// Nothing is externalized: the whole point is that the host adds no deps and no
// React of its own is involved in this subtree.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // A lib build does not auto-replace `process.env.NODE_ENV` the way an app build does,
  // so React's `process.env.NODE_ENV` checks would reach the browser as bare `process`
  // and throw "process is not defined". Define it (and a minimal process.env) at build
  // time so the self-contained bundle has no Node globals.
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
    "process.env": "{}",
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  cacheDir: "node_modules/.vite-widget",
  build: {
    outDir: "dist/widget",
    target: "esnext",
    // Library mode, single self-contained ES module. React/react-dom/@tanstack/
    // axios are intentionally NOT in `rollupOptions.external`, so they are bundled.
    lib: {
      entry: path.resolve(__dirname, "src/federation/widget.tsx"),
      formats: ["es"],
      fileName: () => "exact-trials.js",
    },
  },
});
