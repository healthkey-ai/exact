// Component-suite setup: jest-dom matchers plus a teardown between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  // Filters persist now — through the host's state adapter when there is one,
  // and through `localStorage` when there is not (see `preferences.ts`). jsdom
  // hands every test in a file the same storage, so without this a test that
  // changes a filter seeds the next one's mount and the failure surfaces
  // somewhere unrelated.
  localStorage.clear();
});
