import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { layerRemoteCss } from "./cssLayer";

describe("layerRemoteCss", () => {
  it("puts the sheet in the mf-remote layer so the host's utilities outrank ours", () => {
    const out = layerRemoteCss("@layer utilities{.hidden{display:none}}");
    expect(out).toBe("@layer mf-remote{@layer utilities{.hidden{display:none}}}");
  });

  it("keeps unlayered rules unlayered inside mf-remote", () => {
    const out = layerRemoteCss(".exact-root{color:#111}");
    expect(out).toBe("@layer mf-remote{.exact-root{color:#111}}");
  });

  it("hoists @property and @keyframes, which layers do not affect", () => {
    const out = layerRemoteCss(
      "@property --tw-rotate{syntax:'<angle>';inherits:false}@keyframes pulse{50%{opacity:.5}}@layer utilities{.p-4{padding:1rem}}",
    );
    expect(out.indexOf("@property")).toBeLessThan(out.indexOf("@layer mf-remote"));
    expect(out.indexOf("@keyframes")).toBeLessThan(out.indexOf("@layer mf-remote"));
    expect(out).toContain("@layer mf-remote{@layer utilities{.p-4{padding:1rem}}}");
  });

  it("does not treat an apostrophe in a comment as a string", () => {
    const out = layerRemoteCss("/* the host's own preflight */\n.a{color:red}\n.b{color:blue}");
    expect(out).toContain(".a{color:red}");
    expect(out).toContain(".b{color:blue}");
  });

  it("ignores braces and semicolons inside quoted values", () => {
    const out = layerRemoteCss('.a:after{content:"};"}.b{color:red}');
    expect(out).toBe('@layer mf-remote{.a:after{content:"};"}.b{color:red}}');
  });

  it("treats a backslash as an escape outside strings too", () => {
    // Real class from the compiled bundle. Read as an opening quote, its `\'`
    // swallowed the rest of the sheet and nothing else parsed as top level.
    const css = String.raw`.data-\[selected\=\'true\'\]\:bg-accent{color:red}@keyframes spin{to{rotate:360deg}}`;
    const out = layerRemoteCss(css);
    expect(out.indexOf("@keyframes")).toBeLessThan(out.indexOf("@layer mf-remote"));
    expect(out).toContain(String.raw`@layer mf-remote{.data-\[selected\=\'true\'\]\:bg-accent{color:red}}`);
  });

  it("keeps a bare at-statement with the rest of the sheet", () => {
    const out = layerRemoteCss("@layer theme, base;@layer theme{:root{--x:1}}");
    expect(out).toBe("@layer mf-remote{@layer theme, base;@layer theme{:root{--x:1}}}");
  });

  it("returns nothing but the hoisted rules when there is nothing to layer", () => {
    expect(layerRemoteCss("@keyframes spin{to{rotate:360deg}}")).toBe(
      "@keyframes spin{to{rotate:360deg}}",
    );
  });

  describe("refuses to guess rather than corrupting the cascade", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    function silenceWarn() {
      return vi.spyOn(console, "warn").mockImplementation(() => {});
    }

    it("leaves a sheet with an unbalanced `}` untouched", () => {
      // Clamping depth to zero and carrying on put every remaining rule
      // *outside* the layer — silently, and above everything inside it.
      const warn = silenceWarn();
      const css = ".a{color:red}}.b{color:blue}";
      expect(layerRemoteCss(css)).toBe(css);
      expect(warn).toHaveBeenCalled();
    });

    it("leaves a sheet with an unclosed `{` untouched", () => {
      const warn = silenceWarn();
      const css = ".a{color:red";
      expect(layerRemoteCss(css)).toBe(css);
      expect(warn).toHaveBeenCalled();
    });

    it("leaves a sheet with an unterminated string untouched", () => {
      const warn = silenceWarn();
      const css = '.a:after{content:"oops}';
      expect(layerRemoteCss(css)).toBe(css);
      expect(warn).toHaveBeenCalled();
    });

    it("leaves a sheet with an unterminated comment untouched", () => {
      // Otherwise the layer's closing brace lands inside the comment and the
      // `@layer` block never closes — the browser then drops rules the sheet
      // as written would have applied.
      const warn = silenceWarn();
      const css = ".a{color:red}/*";
      expect(layerRemoteCss(css)).toBe(css);
      expect(warn).toHaveBeenCalled();
    });

    it("leaves a sheet carrying an @import untouched", () => {
      // Wrapped in a layer the @import is invalid and the parser drops it;
      // hoisted out, the imported sheet would outrank our own layer. Neither
      // is a safe silent default.
      const warn = silenceWarn();
      const css = '@import url("fonts.css");.a{color:red}';
      expect(layerRemoteCss(css)).toBe(css);
      expect(warn).toHaveBeenCalled();
    });
  });

  it("hoists @font-face and the non-webkit keyframes prefixes", () => {
    const css =
      "@font-face{font-family:X;src:url(x.woff2)}" +
      "@-moz-keyframes spin{to{rotate:360deg}}" +
      ".a{color:red}";
    const out = layerRemoteCss(css);
    expect(out.indexOf("@font-face")).toBeLessThan(out.indexOf("@layer mf-remote"));
    expect(out.indexOf("@-moz-keyframes")).toBeLessThan(out.indexOf("@layer mf-remote"));
    expect(out).toContain("@layer mf-remote{.a{color:red}}");
  });

  it("documents the top-level-only limit: a nested @keyframes stays in the layer", () => {
    // Not a bug being asserted as correct — a known limit, locked so it is
    // noticed if someone puts an animation inside a media query.
    const css = "@media (min-width:1px){@keyframes k{to{opacity:1}}}";
    expect(layerRemoteCss(css)).toBe(`@layer mf-remote{${css}}`);
  });

  it("is lossless: every byte of the input survives inside the output", () => {
    const css =
      "/* header */@property --x{syntax:'<length>';inherits:false}" +
      "@layer theme, base;.exact-root{--y:1}" +
      "@media (min-width:40rem){.exact-root .grid{display:grid}}";
    const out = layerRemoteCss(css);
    const stripped = out
      .replace(/^@layer mf-remote\{/m, "")
      .replace(/\}$/, "")
      .replace(/@layer mf-remote\{/, "")
      .replace(/\n/g, "");
    for (const rule of ["@property --x", "@layer theme, base;", ".exact-root{--y:1}", "@media (min-width:40rem)"]) {
      expect(stripped).toContain(rule);
    }
  });

  it("still layers the sheet this remote actually ships", () => {
    // Every other test here is a synthetic string, so a bail-out introduced by
    // an edit to exact.css — a top-level @import for a font, an unbalanced
    // brace — would return the sheet unlayered, reinstate the header-collapse
    // this module exists to prevent, and leave the suite green. The only other
    // signal is a console.warn in a browser nobody is watching.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const css = readFileSync(new URL("./exact.css", import.meta.url), "utf8");
    const out = layerRemoteCss(css);
    expect(warn).not.toHaveBeenCalled();
    expect(out).toContain("@layer mf-remote{");
    expect(out).not.toBe(css);
    vi.restoreAllMocks();
  });
});
