/** The guard that reports a test reaching the real download path (#544).
 *
 *  It is installed by `setup.ts` for every component test, and nothing else
 *  in the suite trips it — which is the point, and also the reason it needs
 *  a test of its own: deleted, the whole suite still passes and the thing it
 *  was watching for comes back silently.
 */
import { useEffect } from "react";
import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import {
  assertNoRealDownloads,
  settleTeardown,
  takeDownloads,
  type RealDownload,
} from "./downloadGuard";

/** An anchor with no `href`: jsdom navigates nowhere, so these tests can
 *  click a real one without printing the complaint they exist to prevent. */
const anchor = (attrs: Record<string, string>) => {
  const link = document.createElement("a");
  for (const [name, value] of Object.entries(attrs)) link.setAttribute(name, value);
  // Attached because a click on a detached anchor is not the thing being
  // guarded, and removed again because `cleanup()` only clears what
  // Testing Library rendered.
  document.body.appendChild(link);
  return link;
};

/** A click with no test running at all — a `beforeAll`, or a stray timer
 *  between files.
 *
 *  Taken HERE rather than read back by the first test. Left for a test to
 *  drain, it is drained by whichever test runs first — so `vitest -t` on any
 *  single test in this file failed it, accusing it of a download it never
 *  performed. Which is the bug this whole file exists to stop, reproduced
 *  inside the file itself, on the most ordinary command there is. */
let fromBeforeAll: RealDownload[] = [];

beforeAll(() => {
  const link = anchor({ download: "from-before-all.csv" });
  link.click();
  link.remove();
  fromBeforeAll = takeDownloads();
});

/** Read in source order: three of these assert what the test BEFORE them
 *  left behind, which is the only way to observe a teardown from inside the
 *  suite it runs in. Three of those read what the test BEFORE them left
 *  behind, which is the only way to observe a teardown from inside the
 *  suite it runs in — and all three assert an ABSENCE, so run alone or
 *  shuffled they go vacuous rather than red. Measured: `--sequence.shuffle.tests`
 *  passes 4 runs out of 4, and `vitest -t` passes on every test here.
 *  Vacuous is the price of testing a teardown from inside it; RED for a
 *  guard that is working would be worse, and an earlier shape of the
 *  stamping test did exactly that. */
describe("the download guard", () => {
  it("names no test when there was none", () => {
    expect(fromBeforeAll).toEqual([
      { file: "from-before-all.csv", whileRunning: "(outside a test)" },
    ]);
  });

  it("keeps every download of a test that made several", () => {
    // Taken one at a time, the second would surface in the NEXT test's
    // failure — the guard leaking exactly what it exists to stop.
    for (const file of ["one.csv", "two.csv"]) {
      const link = anchor({ download: file });
      link.click();
      link.remove();
    }

    expect(takeDownloads().map((download) => download.file)).toEqual(["one.csv", "two.csv"]);
  });

  it("turns the records into a failure that names the test", () => {
    // The half the suite cannot exercise on its own: nothing in it ever
    // trips the guard, so gutting the reporting to a bare drain leaves
    // every test green and the guard watching nothing.
    expect(() => assertNoRealDownloads([])).not.toThrow();
    expect(() =>
      assertNoRealDownloads([{ file: "trials.csv", whileRunning: "some > other test" }]),
    ).toThrow(/trials\.csv \(while running: some > other test\)/);
  });

  it("records a download click, and lets it through", () => {
    const link = anchor({ download: "trials.csv" });
    let clicked = 0;
    link.addEventListener("click", () => {
      clicked += 1;
    });

    link.click();

    // Through, not swallowed: swallowing hides a late click from jsdom as
    // well as from this, and then nothing reports it at all.
    expect(clicked).toBe(1);
    // Drained here, so the `afterEach` this file also runs under sees
    // nothing and the test passes on its own terms.
    expect(takeDownloads()).toEqual([
      {
        file: "trials.csv",
        // Stamped at click time: a record whose drain is skipped then
        // surfaces later still carrying this name rather than acquiring
        // the bystander's.
        whileRunning: "the download guard > records a download click, and lets it through",
      },
    ]);
    // And taken once.
    expect(takeDownloads()).toEqual([]);
  });

  it("names the file, so a click blamed on the wrong test still points somewhere", () => {
    anchor({ download: "" }).click();
    expect(takeDownloads()).toEqual([
      {
        file: "(unnamed)",
        whileRunning:
          "the download guard > names the file, so a click blamed on the wrong test still points somewhere",
      },
    ]);
  });

  it("says nothing about an ordinary link", () => {
    const link = anchor({ href: "#somewhere" });
    let clicked = 0;
    link.addEventListener("click", () => {
      clicked += 1;
    });

    link.click();

    expect(clicked).toBe(1);
    expect(takeDownloads()).toEqual([]);
  });
});

/** Which failure a teardown reports, and whether it arrives intact.
 *
 *  Through the function rather than through `it.fails`, which says a test
 *  failed and nothing about what it failed with — so it cannot tell the
 *  teardown failure from the download report, nor the thrown value from a
 *  copy of it. Both mistakes passed the `it.fails` tests below. */
describe("settling a teardown", () => {
  const download = { file: "d.csv", whileRunning: "some > other test" };

  it("reports the unmount failure ahead of the download, and reports IT", () => {
    // The other way round buries a broken unmount under "a real download
    // happened", which is the more urgent news going missing.
    const blewUp = new Error("unmount blew up");
    let caught: unknown = "nothing thrown";
    try {
      settleTeardown(true, blewUp, [download]);
    } catch (error) {
      caught = error;
    }
    // The error itself, not a copy: `throw new Error(String(value))` loses
    // the type and the stack of whatever the test actually threw.
    expect(caught).toBe(blewUp);
  });

  it("rethrows a falsy value as itself", () => {
    let caught: unknown = "nothing thrown";
    try {
      settleTeardown(true, null, []);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeNull();
  });

  it("reports the download when the teardown was clean", () => {
    expect(() => settleTeardown(false, undefined, [download])).toThrow(/d\.csv/);
  });

  it("says nothing when neither happened", () => {
    expect(() => settleTeardown(false, undefined, [])).not.toThrow();
  });
});

/** The other half: the suite's own teardown, which no ordinary test can
 *  exercise because tripping the guard means failing. `it.fails` is what
 *  makes that testable — it counts a throwing hook as the expected failure,
 *  so the pair below asserts the hook DOES throw, and that a teardown of its
 *  own going wrong does not hand the records to the next test. */
describe("the guard's report, through the suite teardown", () => {
  it.fails("fails the test that downloaded, without being drained first", () => {
    const link = document.createElement("a");
    link.setAttribute("download", "unclaimed.csv");
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Deliberately not drained: the teardown has to be the thing that
    // notices. Gut the report to a bare drain and this test stops failing.
  });

  it("leaves nothing behind for the next test", () => {
    expect(takeDownloads()).toEqual([]);
  });

  it.fails("fails the test whose unmount threw, download or no download", () => {
    const Boom = () => {
      useEffect(
        () => () => {
          throw new Error("unmount blew up");
        },
        [],
      );
      return null;
    };
    render(<Boom />);
    const link = document.createElement("a");
    link.setAttribute("download", "while-unmount-throws.csv");
    document.body.appendChild(link);
    link.click();
    link.remove();
  });

  it.fails("still fails a test whose teardown threw and downloaded nothing", () => {
    // The plain teardown failure, which this rework could have swallowed:
    // the records are drained before it is rethrown, and dropping the
    // rethrow would make a broken unmount silently pass.
    const Boom = () => {
      useEffect(
        () => () => {
          throw new Error("unmount blew up");
        },
        [],
      );
      return null;
    };
    render(<Boom />);
  });

  it.fails("keeps a teardown failure that is falsy", () => {
    // `throw null` is legal, and a hook that asks whether the caught value
    // is truthy calls that a clean teardown.
    const Falsy = () => {
      useEffect(
        () => () => {
          // eslint-disable-next-line @typescript-eslint/no-throw-literal
          throw null;
        },
        [],
      );
      return null;
    };
    render(<Falsy />);
  });

  it.fails("clears storage even when the unmount throws", () => {
    // `localStorage.clear()` sat inside the `try` after `cleanup()`, so an
    // unmount that threw skipped it and the next test mounted with the
    // previous one's filters — the same failure-somewhere-else this file is
    // about, one line over.
    localStorage.setItem("exact.filters", "left over");
    const Boom = () => {
      useEffect(
        () => () => {
          throw new Error("unmount blew up");
        },
        [],
      );
      return null;
    };
    render(<Boom />);
  });

  it("does not inherit the storage of the test whose teardown threw", () => {
    expect(localStorage.getItem("exact.filters")).toBeNull();
  });

  it("is not blamed for the download of the test whose teardown threw", () => {
    // The records were drained even though `cleanup()` threw. (Drained
    // inside the `try` instead they would surface in a test that touched
    // nothing — though not necessarily THIS one: the first later test whose
    // own teardown runs is the one that absorbs them, so what kills that
    // mutant is the pair above, not this line. This holds the simpler claim
    // that a thrown teardown does not leak.)
    expect(takeDownloads()).toEqual([]);
  });
});

/** A record that outlives the moment it was made.
 *
 *  The guard stamps each record with the test that was running AT CLICK
 *  TIME, and this is what says so. The click happens in `beforeAll` and the
 *  record is read back INSIDE a test, so a record stamped when it is
 *  drained would carry THIS test's name and one stamped when it is clicked
 *  cannot.
 *
 *  Asserted as "not this test", not as `"(outside a test)"`: vitest does not
 *  clear `currentTestName` between suites, so a `beforeAll` that runs after
 *  some other test has already run sees that test's name rather than
 *  nothing. Which is the same leak, one scope smaller — and pinning the
 *  literal made this test pass alone and fail in the file.
 *
 *  One test in its own `describe`, and the state it needs made by its own
 *  hook: the first shape of this was a pair, where the second test read
 *  what the first had left behind, and `vitest -t` on either of them then
 *  failed a guard that was working perfectly.
 */
describe("a record read back after the moment it was made", () => {
  beforeAll(() => {
    const link = anchor({ download: "from-before-any-test.csv" });
    link.click();
    link.remove();
  });

  it("carries the name it was stamped with, not the one it is read in", () => {
    const [record, ...rest] = takeDownloads();
    expect(rest).toEqual([]);
    expect(record?.file).toBe("from-before-any-test.csv");
    expect(record?.whileRunning).not.toBe(expect.getState().currentTestName);
  });
});
