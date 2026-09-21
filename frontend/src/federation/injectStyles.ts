// Boring CSS injection. One <style> tag, no fonts (host owns typography),
// idempotent across re-mounts. Tokens are scoped under `.exact-root`, so
// a missing one fails visibly inside the remote and doesn't poison the
// host. Mirrors SoC's `injectStyles.ts`.
import css from "./exact.css?inline";
import { layerRemoteCss } from "./cssLayer";

const STYLE_MARKER = 'style[data-mf="exact-remote"]';

export function injectStyles(): void {
  // Keyed on the tag, not on a boolean: a host that sweeps its <head> (a
  // route-level reset, a framework managing <head>) would otherwise leave the
  // remote unstyled for the life of the page, and nothing would put it back.
  if (typeof document === "undefined" || document.querySelector(STYLE_MARKER)) return;
  const style = document.createElement("style");
  style.setAttribute("data-mf", "exact-remote");
  style.textContent = layerRemoteCss(css);
  document.head.appendChild(style);
}

/** Dev-only check that the `--exact-*` tokens this remote depends on are
 *  readable on a mounted `.exact-root`.
 *
 *  It answers exactly ONE question: did `exact.css` reach this page?
 *  Nothing else declares these names, and `.exact-root` carries every one
 *  of them, so a name coming back empty means the sheet is missing and the
 *  remote is rendering unstyled inside its host — a failure that otherwise
 *  shows up as a page that looks wrong and logs nothing.
 *
 *  It cannot check a HOST's overrides, whatever its previous docblock said.
 *  A declaration on the element beats an inherited one in every layer, so
 *  our own `.exact-root` defaults are what a read returns whenever the
 *  sheet is present, no matter what the host set upstream.
 *
 *  Read from `.exact-root` because that is where the declarations are. It
 *  read `:root` instead, reasoning that a component might sit inside a
 *  portal — but `<html>` is where these tokens are NOT, so every name came
 *  back empty and the check reported the whole contract missing every time
 *  it ran. A portal is no obstacle either way: each `.exact-root` carries
 *  the declarations itself, wherever it is mounted.
 *
 *  Nothing mounted yet is not a failure — there is no remote on the page
 *  to check. */
export function assertExactTokens(root?: Element | null): string[] {
  if (typeof document === "undefined") return [];
  const host = root ?? document.querySelector(".exact-root");
  if (!host) return [];
  const required = [
    "--exact-color-primary",
    "--exact-color-eligible",
    "--exact-color-potential",
    "--exact-color-not-eligible",
    "--exact-color-surface",
    "--exact-color-border",
    "--exact-color-text",
    "--exact-color-text-muted",
    // The chosen segment's fill. Its own token because it must move with
    // `--exact-color-primary`, which is painted ON it.
    "--exact-color-primary-50",
  ];
  const styles = getComputedStyle(host);
  return required.filter((name) => !styles.getPropertyValue(name).trim());
}

/** Says it out loud, once per mount, in dev. Lives here rather than in each
 *  caller so the message cannot drift from the check that produces it.
 *
 *  Pass the caller's OWN root: a page with a second EXACT instance on it
 *  would otherwise be answered for by whichever root comes first in the
 *  document, and a root inside a shadow tree is not in `document` at all —
 *  where, notably, a head-injected sheet cannot reach it either. */
export function warnMissingExactTokens(root?: Element | null): void {
  const missing = assertExactTokens(root);
  if (!missing.length) return;
  console.warn(
    `[exact] exact.css did not reach this page — the remote is unstyled. ` +
      `Unreadable tokens: ${missing.join(", ")}`,
  );
}
