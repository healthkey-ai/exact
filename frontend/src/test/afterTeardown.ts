/** Run `act` as if a test's environment were already torn down (#728).
 *
 *  vitest removes jsdom's globals when a file finishes; a timer or promise
 *  started by the test that settles after that and reaches React makes React
 *  DOM read `window.event` and throw "window is not defined" — uncaught from
 *  a timer, unhandled from a promise. This removes the global `window`, runs
 *  `act`, waits `settleMs`, restores it, and returns every error that escaped
 *  by any of the three routes: thrown, uncaught, or unhandled.
 */
export async function errorsAfterTeardown(
  act: () => void | Promise<void>,
  settleMs = 0,
): Promise<string[]> {
  const g = globalThis as { window?: unknown };
  const saved = Object.getOwnPropertyDescriptor(g, "window");
  const escaped: unknown[] = [];
  const onEscape = (reason: unknown) => escaped.push(reason);
  process.on("uncaughtException", onEscape);
  process.on("unhandledRejection", onEscape);
  delete g.window;
  try {
    try {
      await act();
    } catch (thrown) {
      escaped.push(thrown);
    }
    // Real time only when asked for: under fake timers a `setTimeout` wait
    // would never resolve. Microtasks are drained either way.
    if (settleMs > 0) await new Promise((resolve) => setTimeout(resolve, settleMs));
    for (let i = 0; i < 20; i++) await Promise.resolve();
  } finally {
    if (saved) Object.defineProperty(g, "window", saved);
    process.off("uncaughtException", onEscape);
    process.off("unhandledRejection", onEscape);
  }
  return escaped.map(String);
}
