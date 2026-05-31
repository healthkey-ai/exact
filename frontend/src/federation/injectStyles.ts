// Boring CSS injection. One <style> tag, no fonts (host owns typography),
// idempotent across re-mounts. Tokens are scoped under `.exact-root`, so
// a missing one fails visibly inside the remote and doesn't poison the
// host. Mirrors SoC's `injectStyles.ts`.
import css from "./exact.css?inline";

let injected = false;

export function injectStyles(): void {
  if (injected || typeof document === "undefined") return;
  injected = true;
  const style = document.createElement("style");
  style.setAttribute("data-mf", "exact-remote");
  style.textContent = css;
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
  ];
  const styles = getComputedStyle(document.documentElement);
  return required.filter((name) => !styles.getPropertyValue(name).trim());
}
