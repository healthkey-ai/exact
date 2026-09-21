/** The token contract check.
 *
 *  It is a canary for one thing: whether the sheet that declares the
 *  `--exact-*` tokens actually reached the page. Nothing else declares them,
 *  so a name coming back empty means the remote is unstyled — which is a
 *  failure that otherwise shows up as a page that looks wrong and logs
 *  nothing.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { assertExactTokens, warnMissingExactTokens } from "./injectStyles";

/** Every token the check requires, as `exact.css` declares them — on the
 *  element that carries `exact-root`. */
const TOKENS = [
  "--exact-color-primary",
  "--exact-color-eligible",
  "--exact-color-potential",
  "--exact-color-not-eligible",
  "--exact-color-surface",
  "--exact-color-border",
  "--exact-color-text",
  "--exact-color-text-muted",
  "--exact-color-primary-50",
];

const mountRoot = (tokens: string[] = TOKENS) => {
  const root = document.createElement("div");
  root.className = "exact-root";
  for (const name of tokens) root.style.setProperty(name, "#0c5fc0");
  document.body.appendChild(root);
  return root;
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("assertExactTokens", () => {
  it("finds the tokens where they are declared, not on <html>", () => {
    // The trap this check fell into: the tokens are declared on
    // `.exact-root`, and it read `document.documentElement`, where they are
    // not. Every name came back empty, so it reported the entire contract
    // missing every time it ran.
    mountRoot();

    expect(assertExactTokens()).toEqual([]);
  });

  it("names the one that did not arrive", () => {
    mountRoot(TOKENS.filter((name) => name !== "--exact-color-primary-50"));

    expect(assertExactTokens()).toEqual(["--exact-color-primary-50"]);
  });

  it("reads the root it is handed, for a host with more than one on the page", () => {
    mountRoot(TOKENS.filter((name) => name !== "--exact-color-border"));
    const second = mountRoot();

    expect(assertExactTokens(second)).toEqual([]);
  });

  it("says nothing when no remote is mounted", () => {
    // Called before the remote renders — there is nothing on the page to be
    // wrong about, and a list of nine "missing" tokens would be noise.
    expect(assertExactTokens()).toEqual([]);
  });
});

describe("warnMissingExactTokens", () => {
  it("says what a missing token actually means", () => {
    // Not "the host forgot an override": a host CANNOT strip one of these.
    // Its own declarations on `.exact-root` are what a read returns while
    // the sheet is present, so an empty one means the sheet is not.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mountRoot(TOKENS.filter((name) => name !== "--exact-color-border"));

    warnMissingExactTokens();

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toMatch(/exact\.css did not reach this page/);
    expect(warn.mock.calls[0][0]).toContain("--exact-color-border");
    warn.mockRestore();
  });

  it("stays quiet on a styled page", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mountRoot();

    warnMissingExactTokens();

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
});
