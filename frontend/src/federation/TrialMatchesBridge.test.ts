import { describe, expect, it } from "vitest";

import { AxiosHeaders } from "axios";

import provider, {
  buildClient,
  RESOLVE_TIMEOUT_MS,
  SEARCH_TIMEOUT_MS,
} from "./TrialMatchesBridge";

describe("the exposed provider", () => {
  it("is memoised, so render() and destroy() share one rootMap", () => {
    // bridge-react's own factory is `() => { const rootMap = new Map(); … }`,
    // and `destroy` no-ops when the node is not in *its* map. The documented
    // `provider().render(…)` / `provider().destroy(…)` pair therefore
    // unmounted nothing: the React tree, its popstate listener and its
    // QueryClient survived, and the next mount called createRoot() on a
    // container that already had a live root.
    expect(provider()).toBe(provider());
  });

  it("exposes both halves of the mount contract", () => {
    const p = provider();
    expect(typeof p.render).toBe("function");
    expect(typeof p.destroy).toBe("function");
  });
});

describe("buildClient", () => {
  const noToken = async () => undefined;

  it("resolves the baseURL from the origin and the base path", () => {
    expect(buildClient("https://exact.example/", "api", noToken, undefined).defaults.baseURL).toBe(
      "https://exact.example/api",
    );
  });

  it("leaves the search client untimed", () => {
    // axios normalises an absent timeout to 0, which means "no timeout" — not
    // a zero-length abort. Asserted through the constant, so that a timeout
    // reintroduced here fails rather than quietly multiplying by react-query's
    // three retries.
    expect(SEARCH_TIMEOUT_MS).toBeUndefined();
    expect(buildClient("https://e", "", noToken, SEARCH_TIMEOUT_MS).defaults.timeout).toBe(0);
  });

  it("bounds the resolution, which blocks the whole screen", () => {
    expect(RESOLVE_TIMEOUT_MS).toBe(10_000);
    expect(buildClient("https://e", "", noToken, RESOLVE_TIMEOUT_MS).defaults.timeout).toBe(
      10_000,
    );
  });

  it("attaches the token as a Bearer header", async () => {
    const client = buildClient("https://e", "", async () => "tok-1", undefined);
    const config = await runRequestInterceptor(client);
    expect(config.headers.Authorization).toBe("Bearer tok-1");
  });

  it("sends no Authorization header when the host has no token", async () => {
    // Better than an empty `Bearer `: the server sees an anonymous request
    // and says so, rather than rejecting a malformed credential.
    const client = buildClient("https://e", "", noToken, undefined);
    const config = await runRequestInterceptor(client);
    expect(config.headers.Authorization).toBeUndefined();
  });

  it("propagates a rejection from the token reader, so the request never goes out", async () => {
    const client = buildClient("https://e", "", async () => {
      throw new Error("session changed");
    }, undefined);
    await expect(runRequestInterceptor(client)).rejects.toThrow("session changed");
  });
});

/** Drive the request interceptor without a network round trip. */
async function runRequestInterceptor(
  client: ReturnType<typeof buildClient>,
): Promise<{ headers: Record<string, unknown> }> {
  const handlers: { fulfilled: (c: unknown) => unknown }[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (client.interceptors.request as any).forEach((h: { fulfilled: (c: unknown) => unknown }) =>
    handlers.push(h),
  );
  // A real AxiosHeaders, not a bare object: if axios ever required `.set()`,
  // a plain object would keep these tests green while every real request went
  // out unauthenticated.
  let config: unknown = { headers: new AxiosHeaders() };
  for (const handler of handlers) config = await handler.fulfilled(config);
  return config as { headers: Record<string, unknown> };
}
