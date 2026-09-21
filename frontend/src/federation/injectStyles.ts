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

/** Dev-only sanity check that the host (or this remote) actually set the
 *  `--exact-*` tokens we depend on. Per hk-labs module-federation.md
 *  recommendation — call from the harness during dev to catch missing
 *  token overrides early. Portal-safe: reads tokens from `:root`, not
 *  from a component that may sit inside a dialog/portal where computed
 *  styles see the dialog's own root. */
export function assertExactTokens(): string[] {
  if (typeof document === "undefined") return [];
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
  const styles = getComputedStyle(document.documentElement);
  return required.filter((name) => !styles.getPropertyValue(name).trim());
}
