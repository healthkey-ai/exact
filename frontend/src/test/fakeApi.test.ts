// The harness tests itself, for the one part of it with moving parts.
//
// `deferNextList` holds a response open so a test can assert what the reader
// sees WHILE a request is in flight. Two bugs in its first version — a
// `trial_ids` request slipping past the deferral, and a release issued before
// the request arrived leaving that request unresolved for ever — would both
// have shown up as a test that TIMED OUT rather than failed, in whichever
// test used the feature next. That is the least useful way for a test to be
// wrong, so the mechanism gets its own coverage.
import { describe, expect, it } from "vitest";

import { fakeApi } from "./renderTrialMatches";

/** Whether `promise` has resolved by now.
 *
 *  A macrotask, not a couple of microtask turns. Two turns are enough for an
 *  already-resolved promise and not for anything with one more hop —
 *  a thenable being adopted, a `setTimeout(0)`, a three-link `.then` chain —
 *  all of which HAVE resolved and would be reported as pending. The
 *  `toBe(false)` assertions below would then hold for a fake that resolves a
 *  tick later rather than for one that is genuinely held, which is the
 *  coverage this file exists to provide. A timer turn drains the microtask
 *  queue on the way, so it answers both directions.
 */
const settled = async (promise: Promise<unknown>) => {
  let done = false;
  void promise.then(() => {
    done = true;
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  return done;
};

describe("deferNextList", () => {
  it("holds the next list response until it is released", async () => {
    const api = fakeApi();
    const release = api.deferNextList();

    const response = api.client.post("/trials/search/match/", {});
    expect(await settled(response)).toBe(false);

    release();
    expect(await settled(response)).toBe(true);
  });

  it("releases a request that had not arrived yet", async () => {
    // The order a test naturally writes when the request is triggered by a
    // click: arm, act, and only then release. Before, this left the request
    // unresolved and the test hung.
    const api = fakeApi();
    const release = api.deferNextList();
    release();

    const response = api.client.post("/trials/search/match/", {});
    expect(await settled(response)).toBe(true);
  });

  it("defers a state tab's narrowed request too", async () => {
    // A `trial_ids` request is a list request. Deferring only the unnarrowed
    // ones would make every state-tab test silently skip the deferral and
    // assert against an already-resolved response.
    const api = fakeApi();
    const release = api.deferNextList();

    const response = api.client.post("/trials/search/match/", { trial_ids: ["1"] });
    expect(await settled(response)).toBe(false);

    release();
    expect(await settled(response)).toBe(true);
  });

  it("still narrows by trial_ids once released", async () => {
    // Non-vacuity: the deferral must not swallow the payload it defers.
    const api = fakeApi();
    api.setResponse({ results: [] });
    const release = api.deferNextList();

    const response = api.client.post("/trials/search/match/", { trial_ids: [] });
    release();

    const { data } = (await response) as { data: { itemsTotalCount: number } };
    expect(data.itemsTotalCount).toBe(0);
  });

  it("leaves the request after it alone", async () => {
    const api = fakeApi();
    const release = api.deferNextList();
    const first = api.client.post("/trials/search/match/", {});
    const second = api.client.post("/trials/search/match/", {});

    expect(await settled(second)).toBe(true);
    expect(await settled(first)).toBe(false);

    // The release still has to work after a later request has come and gone.
    // Without this the test would pass against a deferral that can never be
    // released at all, and leave a pending promise behind it.
    release();
    expect(await settled(first)).toBe(true);
  });

  it("does not swallow a request it was not armed for", async () => {
    // Only the list endpoints consume the deferral. An unrecognised path
    // taking it would answer the list immediately while the test waited for
    // a state it had already passed through.
    const api = fakeApi();
    const release = api.deferNextList();

    const other = api.client.post("/normalize-ctomop-row/", {});
    expect(await settled(other)).toBe(true);

    const list = api.client.post("/trials/search/match/", {});
    expect(await settled(list)).toBe(false);
    release();
    expect(await settled(list)).toBe(true);
  });

  it("answers with the data as it stood when the request was made", async () => {
    // A `setResponse` while a request is held describes the NEXT response.
    // Resolving the payload late would let a test retroactively change a
    // response the server had already been asked for.
    const api = fakeApi();
    api.setResponse({ itemsTotalCount: 11 });
    const release = api.deferNextList();
    const held = api.client.post("/trials/search/match/", {});

    api.setResponse({ itemsTotalCount: 22 });
    release();

    const { data } = (await held) as { data: { itemsTotalCount: number } };
    expect(data.itemsTotalCount).toBe(11);
  });

  it("hands back an object of its own, not the fake's live one", async () => {
    // The snapshot is shallow-copied so the held answer cannot be edited
    // through a reference something else holds. (The rows inside are still
    // the array the test supplied — nothing mutates a response in place, and
    // deep-copying would break the identity comparisons several tests make on
    // `trial()` objects.)
    const api = fakeApi();
    const release = api.deferNextList();
    const held = api.client.post("/trials/search/match/", {});
    release();

    const first = (await held) as { data: { itemsTotalCount: number } };
    first.data.itemsTotalCount = 999;

    const after = (await api.client.post("/trials/search/match/", {})) as {
      data: { itemsTotalCount: number };
    };
    expect(after.data.itemsTotalCount).not.toBe(999);
  });

  it("refuses a second arm while the first request is still held", async () => {
    // The guard has to outlive the request's ARRIVAL, not just precede it.
    // `deferList` is cleared the moment the request comes in, so a guard on
    // that alone lets a second arm through in exactly the window a test is
    // most likely to be in: holding one request and setting up the next.
    const api = fakeApi();
    const release = api.deferNextList();
    const held = api.client.post("/trials/search/match/", {});
    expect(await settled(held)).toBe(false);

    expect(() => api.deferNextList()).toThrow(/already armed/);

    release();
    expect(await settled(held)).toBe(true);
    // ...and arming again once released is fine.
    const second = api.deferNextList();
    second();
  });

  it("refuses to be armed twice, rather than hanging", async () => {
    // The second arm used to replace the first closure, so the first
    // release flipped a flag nobody read and its request hung — a test that
    // times out instead of failing.
    const api = fakeApi();
    const release = api.deferNextList();
    expect(() => api.deferNextList()).toThrow(/already armed/);
    release();
  });
});
