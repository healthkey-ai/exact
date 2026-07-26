import dns from "node:dns";
import path from "node:path";
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { federation } from "@module-federation/vite";

dns.setDefaultResultOrder("ipv4first");

// When the dev:remote server is hit at `/`, serve the dev harness instead of
// the SPA index. This lets `npm run dev:remote` boot a standalone surface
// that exercises the exposed `./TrialMatches` module exactly the way a host
// would consume it.
function devHarnessRedirect(): Plugin {
  return {
    name: "exact-dev-harness-redirect",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url === "/" || req.url === "/index.html") {
          req.url = "/dev-harness.html";
        }
        next();
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxyTarget = env.VITE_EXACT_API_PROXY_TARGET || "http://localhost:8000";
  // Two named PROMOP backends, each behind its own proxy path so the dev
  // harness can flip between them via a tab without losing the staging
  // session when local is running. Mirror SoC's naming so the harness
  // ports cleanly.
  const promopLocalTarget =
    env.VITE_PROMOP_LOCAL_TARGET || "http://localhost:8001";
  const promopStagingTarget =
    env.VITE_PROMOP_STAGING_TARGET || "https://ctomop.onrender.com";

  // Shared http-proxy cookie-rewrite. Strips Secure/SameSite=None and
  // rescopes Path=/ to the proxy mount point so plain-http localhost dev
  // keeps a working session cookie AND PROMOP cookies don't leak to every
  // other localhost service.
  const makeCookieRewriter = (mountPath: string): ProxyOptions["configure"] => {
    const rewriteCookie = (raw: string): string =>
      raw
        .replace(/;\s*Secure/gi, "")
        .replace(/;\s*SameSite=None/gi, "; SameSite=Lax")
        .replace(/(;\s*Path=)\/(?=;|$)/gi, `$1${mountPath}`);

    return (proxy) => {
      proxy.on("proxyRes", (proxyRes) => {
        const sc = proxyRes.headers["set-cookie"];
        if (Array.isArray(sc)) {
          proxyRes.headers["set-cookie"] = sc.map(rewriteCookie);
        } else if (typeof sc === "string") {
          proxyRes.headers["set-cookie"] = [rewriteCookie(sc)];
        }
      });
    };
  };

  return {
    plugins: [
      devHarnessRedirect(),
      react(),
      tailwindcss(),
      federation({
        name: "exact_remote",
        filename: "remoteEntry.js",
        exposes: {
          "./TrialMatches": "./src/federation/TrialMatches.tsx",
          "./types": "./src/federation/types.ts",
        },
        // Same singletons as SoC / hk-labs so a host that loads multiple
        // remotes shares one React tree and one query cache. No
        // radix/recharts yet — add them here when this remote uses them.
        shared: {
          react: { singleton: true, strictVersion: false },
          "react-dom": { singleton: true, strictVersion: false },
          "react/jsx-runtime": { singleton: true, strictVersion: false },
          "react/jsx-dev-runtime": { singleton: true, strictVersion: false },
          "@tanstack/react-query": { singleton: true, strictVersion: false },
          // axios deliberately NOT shared. The @module-federation/vite
          // 1.15.5 dev-mode shim wraps the axios default export through
          // a `__mfNormalizeShareModule` peeling pass that mishandles
          // axios's identity (its `default` self-reference combined with
          // its `function`-typed value). The net effect at runtime: the
          // host imports axios, calls `axios.create(...)`, and gets an
          // instance whose method properties (`get`, `post`, …) are
          // stripped — `apiClient.get is not a function`. Symptom-only
          // when running `dev:remote` from a remote whose deps include
          // axios; SoC isn't affected because it never calls `axios.create`
          // on imported axios in code paths that the shim reaches.
          //
          // Loading axios normally (no federation shim) fixes the issue.
          // Hosts that mount this remote will end up with two axios
          // copies — fine because axios has no module-level state worth
          // sharing (interceptors and defaults are per-instance, not
          // per-module). Re-add when @module-federation/vite ships a
          // fix for function-typed shared defaults.
        },
        dts: false,
      }),
    ],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    cacheDir: "node_modules/.vite-remote",
    build: {
      outDir: "dist/remote",
      target: "esnext",
      // Explicit empty input: only the federation plugin's `remoteEntry.js`
      // + exposed modules end up in `dist/remote/`. Without this, Vite
      // auto-discovers `index.html` and would pull in the dev harness +
      // any future fixtures into the published bundle — leaking harness
      // assets to every host that mounts the remote. Mirrors SoC.
      rollupOptions: { input: {} },
    },
    server: {
      port: 5177,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ""),
        },
        // PROMOP proxies for the dev harness:
        //   /promop-local/api/...   → http://localhost:8001
        //   /promop-staging/api/... → https://ctomop.onrender.com
        // Going same-origin via the proxy is the only path where PROMOP's
        // SESSION_COOKIE_SECURE = True cookie survives a plain-http dev
        // loop. Set-Cookie is rewritten so the cookie scopes to the
        // proxy mount (sessions don't collide between local + staging).
        "/promop-local": {
          target: promopLocalTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/promop-local/, ""),
          configure: makeCookieRewriter("/promop-local"),
        },
        "/promop-staging": {
          target: promopStagingTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/promop-staging/, ""),
          configure: makeCookieRewriter("/promop-staging"),
        },
      },
    },
  };
});
