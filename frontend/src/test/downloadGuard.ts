import { expect } from "vitest";

/** Downloads a test performed for real.
 *
 *  The CSV export hands its file over with `link.click()` on an anchor
 *  carrying a `blob:` href and a `download` attribute. jsdom answers a real
 *  one with "Not implemented: navigation to another Document" — through its
 *  virtual console and asynchronously, so the complaint is printed against
 *  whatever test is RUNNING by then rather than the one that caused it. A
 *  test that starts an export without waiting for the file leaves it for a
 *  stranger: measured before this, one per run of `export.test.tsx`, with
 *  the complaint landing two tests later (#544).
 *
 *  Recorded, and the click still performed. Swallowing it was the first
 *  shape of this and it was worse: a click scheduled by the LAST test in a
 *  file lands after the last `afterEach`, so nothing reports it — and
 *  swallowing removed jsdom's own complaint as well, trading noise for
 *  silence in exactly the fire-and-forget case this exists to catch.
 *
 *  Each record carries the test that was RUNNING when the click happened —
 *  which is not the same claim as who started the export, and must not be
 *  written as if it were. Two cases:
 *
 *  A click that lands inside the test that made it names that test, and
 *  that is the #544 case. A click that lands later — the export was started
 *  and not waited for — names whichever test was running by then, exactly
 *  as jsdom's own complaint did. What is gained there is not the culprit
 *  but the FILE: a download nobody expected, named, in a suite where only
 *  one path produces one.
 *
 *  The name is stamped at click time rather than at drain time because the
 *  two come apart in a third way: a hook registered later than ours can
 *  throw and skip the drain (a `describe`-level `afterEach`, or `cleanup()`
 *  itself — vitest stops the remaining hooks), and the record then surfaces
 *  in a test that had nothing to do with any of it. Stamped late, it would
 *  acquire that test's name and be believed. */
export interface RealDownload {
  /** The `download` attribute, which is the filename the reader would get. */
  file: string;
  /** The test that was running when the click happened. NOT necessarily the
   *  test that started the export: see above. */
  whileRunning: string;
}

const performed: RealDownload[] = [];
let installed = false;

/** Install once, from the suite setup. */
export function installDownloadGuard(): void {
  // Once. Under `--no-isolate` the module registry is shared, so a second
  // install would wrap the first and record one click twice per test file.
  //
  // A flag of our own, not the name of whatever is on the prototype: by the
  // time another file's setup runs, `export.test.tsx`'s file-level stub may
  // be in force, and a name check would then see `stubbedDownload`, decide
  // the guard is absent, and install a second one ON TOP of the stub —
  // clicks recorded and swallowed at once. (`--no-isolate` is not supported
  // regardless: vitest leaks `currentTestName` across files, so a click from
  // a `beforeAll` gets the previous file's test name.)
  if (installed) return;
  installed = true;
  const passThrough = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function guardedClick(this: HTMLAnchorElement) {
    // `download` only: that is what makes an anchor a file handover rather
    // than a link, and a test clicking an ordinary link is not this bug.
    // Other routes to the same jsdom complaint — a `blob:` href with no
    // `download`, a link to another document, a form submit — are not
    // covered, and none is reachable in this suite today.
    if (this.hasAttribute("download")) {
      performed.push({
        file: this.getAttribute("download") || "(unnamed)",
        whileRunning: expect.getState().currentTestName ?? "(outside a test)",
      });
    }
    return passThrough.call(this);
  };
}

/** What has been recorded since the last call, and forget it. */
export function takeDownloads(): RealDownload[] {
  return performed.splice(0);
}

/** Turn those records into a failure. Separate from the hook that calls it so
 *  that the half which does the reporting has a test of its own: gutted to a
 *  bare `takeDownloads()`, the hook drains the records and says nothing, and
 *  the whole suite stays green while the guard watches nothing. */
export function assertNoRealDownloads(downloads: RealDownload[]): void {
  if (downloads.length === 0) return;
  const who = downloads
    .map((download) => `${download.file} (while running: ${download.whileRunning})`)
    .join("; ");
  throw new Error(
    `A real download happened: ${who}. jsdom answers such a click with a ` +
      "navigation it cannot perform, and prints the complaint against " +
      "whichever test is running by then — which is why this is a failure " +
      "rather than a line of noise. If the test named above exports, stub " +
      "`HTMLAnchorElement.prototype.click` for its file, as export.test.tsx " +
      "does. If it does not, the click arrived from an export an EARLIER " +
      "test started and did not wait for, and the file named is the way to " +
      "find it (#544).",
  );
}

/** What a teardown reports, given what happened in it.
 *
 *  A pure function rather than three lines inside the hook, because the
 *  claims worth making here are about WHICH error surfaces and whether it
 *  arrives intact — and the only instrument the hook itself offers is
 *  `it.fails`, which says a test failed and nothing about what it failed
 *  with. Both mistakes below passed four `it.fails` tests: reporting the
 *  download ahead of the teardown failure (burying a broken unmount under
 *  "a real download happened"), and rethrowing `new Error(String(value))`
 *  (losing the type and stack of whatever was actually thrown).
 *
 *  The teardown failure wins: a test whose unmount blew up has a problem
 *  that outranks the bookkeeping, and the download records are drained
 *  before this is called either way, so nothing is carried into the next
 *  test by reporting it later. */
export function settleTeardown(
  teardownThrew: boolean,
  teardownFailure: unknown,
  downloads: RealDownload[],
): void {
  // The flag, not the value: `throw null` and `throw 0` are legal, and a
  // check on the value alone would call them a clean teardown.
  if (teardownThrew) throw teardownFailure;
  assertNoRealDownloads(downloads);
}
