/** The widget entry — the build CancerBot actually executes.
 *
 *  CB loads `exact-trials.js` as a vendored bundle and calls `mount(el, …)`
 *  across a React boundary (its `ui/` is React 18; this bundles its own 19).
 *  Nothing else in this suite covers that seam, and it is imperative code:
 *  the host is responsible for calling `unmount`, so the contract it is
 *  promised — idempotent mount, a disposer that really disposes, a safe
 *  unmount — is what these tests hold.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
// From Testing Library, not from `react`: it is the same `act`, plus the
// `IS_REACT_ACT_ENVIRONMENT` flag React wants set, which this suite's setup
// leaves to it.
import { act, waitFor } from "@testing-library/react";

import { injectStyles } from "./injectStyles";
import { mount, unmount } from "./widget";

// Wraps the real thing: the widget must be the caller that sets the terms,
// and every later call (TrialMatches, the detail page) must still find the
// tag and return.
vi.mock("./injectStyles", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./injectStyles")>();
  return { ...actual, injectStyles: vi.fn(actual.injectStyles) };
});

const host = () => document.getElementById("host") as HTMLElement;

afterEach(() => {
  act(() => unmount(host()));
  document.body.innerHTML = "";
  // The injected sheets live in <head>, so a test that does not clear them
  // hands the next one a page that is already styled — and a spy left on
  // `console.error` by a failing test would outlive it.
  document.head.querySelectorAll("style[data-mf]").forEach((tag) => tag.remove());
  vi.restoreAllMocks();
});

const givenAHost = () => {
  const el = document.createElement("div");
  el.id = "host";
  document.body.appendChild(el);
  return el;
};

describe("the widget's stylesheet", () => {
  it("is injected unlayered, because this host will not declare our layer", () => {
    // The remote wraps its sheet in `@layer mf-remote` so it cannot outrank a
    // host's own chrome. A host that needs THIS build cannot share our React
    // tree, and will not be declaring that layer either — CB's `ui/` ships
    // Tailwind v3's unlayered preflight, which per the cascade spec beats
    // every layered rule whatever its specificity. Measured in a browser
    // against that preflight: the CTA lost its background, the title rendered
    // at 14px/400, the segmented controls lost their padding.
    //
    // Asserted on the call rather than on the tag: `exact.css?inline` is
    // empty under vitest, so the injected sheet's text distinguishes nothing.
    const el = givenAHost();

    act(() => {
      mount(el);
    });

    expect(vi.mocked(injectStyles)).toHaveBeenCalledWith({ layered: false });
  });

  it("injects the sheet once, not once per mode", () => {
    // TrialMatches injects on mount too. Keyed only on the tag, its call
    // found no LAYERED tag and appended a second full copy of the sheet —
    // sixty kilobytes twice in the host's head. Within one bundle the mode
    // is decided once.
    const el = givenAHost();

    act(() => {
      mount(el);
    });

    expect(document.head.querySelectorAll("style[data-mf]")).toHaveLength(1);
    expect(document.querySelector('style[data-mf="exact-remote-unlayered"]')).not.toBeNull();
  });

  it("does not decide the cascade for a build sharing the page", () => {
    // Both builds can be on one document: the federation remote in the
    // host's React tree, this one where that tree cannot reach. They are
    // separate bundles, so the other one's call arrives with its own mode —
    // spelled out here, since one test process has one module instance. The
    // direction that hurts is the remote left unlayered, free to outrank the
    // host's own chrome.
    const el = givenAHost();

    act(() => {
      mount(el);
    });
    injectStyles({ layered: true });

    expect(document.querySelector('style[data-mf="exact-remote-unlayered"]')).not.toBeNull();
    expect(document.querySelector('style[data-mf="exact-remote"]')).not.toBeNull();
  });
});

describe("the settings seam", () => {
  it("reaches the component, which is the only thing this option does", async () => {
    // The seam it crosses fails silently when it is wrong: the host keeps
    // answering its own calls, the page keeps rendering, and nothing is
    // remembered. The option is one line in `mount`, and one line is what
    // gets dropped.
    const el = givenAHost();
    const getPreferences = vi.fn(async () => ({ searchTitle: "from-the-host" }));

    act(() => {
      mount(el, {
        preferences: {
          getPreferences,
          savePreferences: async () => {},
          resetPreferences: async () => {},
        },
      });
    });

    await waitFor(() => expect(getPreferences).toHaveBeenCalled());
  });
});

describe("mount", () => {
  it("renders the trials surface into the element it is given", () => {
    const el = givenAHost();

    act(() => {
      mount(el);
    });

    // The remote's own root, with its scoped stylesheet injected beside it.
    expect(el.querySelector(".exact-root")).not.toBeNull();
    // This build's tag, which is the unlayered one.
    expect(document.querySelector('style[data-mf="exact-remote-unlayered"]')).not.toBeNull();
  });

  it("replaces a previous mount on the same element rather than stacking on it", () => {
    // A host that re-mounts — CB's page re-runs its effect when the token or
    // the person changes — must not end up with two React roots writing to
    // the same node. React says so out loud in dev; the damage is that the
    // first root keeps its queries running behind the second.
    const el = givenAHost();
    // The DOM alone does not show this: the second root replaces what is on
    // screen, and the first goes on living behind it with its queries and
    // its effects. React says so, and that is what this listens for.
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});

    act(() => {
      mount(el);
    });
    act(() => {
      mount(el);
    });

    expect(el.querySelectorAll(".exact-root")).toHaveLength(1);
    expect(errors.mock.calls.map(String).join(" ")).not.toMatch(/already been passed to createRoot/);
  });

  it("hands back a disposer that empties the element", () => {
    const el = givenAHost();
    let dispose = () => {};

    act(() => {
      dispose = mount(el);
    });
    act(() => {
      dispose();
    });

    expect(el.childElementCount).toBe(0);
  });

  it("gives back a disposer for ITS mount, not for whatever is there later", () => {
    // Host lifecycles do not promise an order: a re-mount can land before the
    // previous cleanup runs. A disposer that unmounts "the element" would
    // then take down the live mount and blank the page.
    const el = givenAHost();
    let first = () => {};

    act(() => {
      first = mount(el);
    });
    act(() => {
      mount(el);
    });
    act(() => {
      first();
    });

    expect(el.querySelector(".exact-root")).not.toBeNull();
  });

  it("disposes twice without complaint", () => {
    // The host may call the disposer and then unmount on teardown; neither
    // order is wrong, and neither may throw into the host's own lifecycle.
    const el = givenAHost();
    let dispose = () => {};

    act(() => {
      dispose = mount(el);
    });
    act(() => {
      dispose();
    });

    expect(() => act(() => dispose())).not.toThrow();
    expect(() => act(() => unmount(el))).not.toThrow();
  });

  it("unmounts an element it never mounted, quietly", () => {
    expect(() => unmount(givenAHost())).not.toThrow();
  });
});
