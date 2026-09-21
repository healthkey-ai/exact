// Boring CSS injection. One <style> tag, no fonts (host owns typography),
// idempotent across re-mounts. Tokens are scoped under `.exact-root`, so
// a missing one fails visibly inside the remote and doesn't poison the
// host. Mirrors SoC's `injectStyles.ts`.
import css from "./exact.css?inline";
import { layerRemoteCss } from "./cssLayer";

/** One tag per LAYERING MODE, not one per remote.
 *
 *  A page can carry both builds — the federation remote in the host's own
 *  React tree and this remote's standalone widget somewhere that cannot
 *  share it. Keyed on one marker, whichever injected first would decide the
 *  cascade for both: the widget left layered under a host preflight that
 *  outranks it, or worse, the remote left UNLAYERED, where it can outrank
 *  the host's own chrome — the failure `cssLayer.ts` exists to prevent.
 *  Two tags, two answers, and each build finds its own. */
const markerFor = (layered: boolean) =>
  layered ? 'style[data-mf="exact-remote"]' : 'style[data-mf="exact-remote-unlayered"]';

/** What the first call in THIS module instance asked for. A bundle carries
 *  its own copy of this module, so "this instance" is "this build" — the
 *  widget's entry sets the mode once and every later call inside the same
 *  bundle (TrialMatches, the detail page) inherits it instead of injecting a
 *  second copy of the sheet in the other mode. */
let chosenMode: boolean | null = null;

export function injectStyles(options: { layered?: boolean } = {}): void {
  const layered = options.layered ?? chosenMode ?? true;
  chosenMode = layered;
  // Keyed on the tag, not on a boolean: a host that sweeps its <head> (a
  // route-level reset, a framework managing <head>) would otherwise leave the
  // remote unstyled for the life of the page, and nothing would put it back.
  // `chosenMode` above is what survives that sweep — the re-injection has to
  // come back in the same mode, not in the default one.
  if (typeof document === "undefined" || document.querySelector(markerFor(layered))) return;
  const style = document.createElement("style");
  style.setAttribute("data-mf", layered ? "exact-remote" : "exact-remote-unlayered");
  // `layered: false` is for the self-contained widget build, and only for it.
  // The layer exists so a remote sharing a host's page cannot outrank the
  // host's own chrome — but per the cascade-layers spec an UNLAYERED host
  // rule beats every layered one regardless of specificity, and a host that
  // cannot share a React tree with us (CB's React-18 `ui/`, which ships
  // Tailwind v3's unlayered preflight) will not be declaring our layer order
  // either. Measured there: the CTA loses its background, the title renders
  // at 14px/400, the segmented controls lose their padding.
  //
  // Safe to drop for that build because this sheet is scoped: every selector
  // is under `.exact-root`, and it ships no Tailwind utilities (see the head
  // of `exact.css`), so unlayered it still cannot reach anything of the
  // host's.
  style.textContent = layered ? layerRemoteCss(css) : css;
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
    // The chosen segment's fill and the chosen tab count's edge. In this
    // list for the only reason anything is in it — a name that reads empty
    // means the sheet is gone. It does NOT check that a host which themed
    // `--exact-color-primary` themed these with it: our own defaults are
    // declared on the same element, so they read back whatever the host did
    // upstream. Nothing here can see that pairing come apart.
    "--exact-color-primary-50",
    "--exact-color-primary-200",
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
