// Component-suite setup: jest-dom matchers plus a teardown between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { installDownloadGuard, settleTeardown, takeDownloads } from "./downloadGuard";

/** One hook, not two, and the order inside it is the point.
 *
 *  It fixes the order between THESE two things and nothing more: a hook
 *  registered later still runs first and, if it throws, skips this one
 *  entirely — the next test then inherits a mounted tree, the previous
 *  test's storage, and its download records. Which is why each record
 *  carries the test that made it.
 *
 *  `cleanup()` first, then the drain and the clearing that must happen
 *  whatever it did, and the report last — see `settleTeardown` for which
 *  failure wins. Written as two hooks, that order held only by accident of
 *  vitest's default `sequence.hooks: "stack"` — reverse registration order
 *  — and a hook that fails stops the ones still to run, so under `list` the
 *  report would have skipped `cleanup()` and handed the next test a mounted
 *  tree: one cross-test failure traded for another. In one hook the order
 *  is in the code, where no setting reaches it.
 *
 *  A test that reached the real download path is a test that will be blamed
 *  somewhere else — see `downloadGuard.ts`. Reported here instead, naming
 *  the test that was RUNNING when the click happened: the one that did it
 *  when the click is synchronous, and a signpost to the file when it is
 *  not. */
installDownloadGuard();

afterEach(() => {
  // A flag beside the value, not the value's truthiness: `throw null` and
  // `throw 0` are legal, and a hook that tested the value alone would treat
  // them as a clean teardown — quietly dropping a failure the previous hook
  // propagated.
  let teardownThrew = false;
  let teardownFailure: unknown;
  try {
    cleanup();
  } catch (error) {
    teardownThrew = true;
    teardownFailure = error;
  }
  // Both of these run whatever the unmount did, so a component that throws
  // on its way out cannot hand the next test either the previous one's
  // filters or the previous one's downloads — both of which surface as a
  // failure somewhere unrelated, which is the thing this whole file is
  // about.
  //
  // Filters persist now — through the host's state adapter when there is
  // one, and through `localStorage` when there is not (see
  // `preferences.ts`). jsdom hands every test in a file the same storage.
  // Drained BEFORE the storage is cleared, so a `localStorage` that throws
  // — a test that replaced it with a stub lacking `clear`, which the node
  // suite already does — cannot take the records with it.
  const downloads = takeDownloads();
  try {
    localStorage.clear();
  } catch (error) {
    // Kept unless the unmount already failed: the first failure is the one
    // that explains the rest.
    if (!teardownThrew) {
      teardownThrew = true;
      teardownFailure = error;
    }
  }
  settleTeardown(teardownThrew, teardownFailure, downloads);
});
