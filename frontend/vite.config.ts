import dns from "node:dns";
import path from "node:path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Resolve `localhost` to 127.0.0.1 (IPv4) before ::1 so the Vite dev proxy
// reaches Django's runserver, which only binds to IPv4 by default.
dns.setDefaultResultOrder("ipv4first");

// SPA build / dev server. Used for `npm run dev` (standalone SPA) and
// `npm run build:spa` (host-agnostic harness output). The federated remote
// is built by `vite.remote.config.ts`.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxyTarget = env.VITE_EXACT_API_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      port: 5178,
      strictPort: true,
      proxy: {
        // EXACT's API runs on Django; the harness calls
        //   /api/trials/...      → http://localhost:8000/trials/...
        //   /api/form-settings/  → http://localhost:8000/form-settings/
        // The proxy strips the `/api` mount so Django sees its own paths.
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
        },
      },
    },
  };
});
