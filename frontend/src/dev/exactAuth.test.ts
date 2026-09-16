// What the reader is told when sign-in fails.
//
// This is selection logic, not formatting: four sources compete for one
// string (the server's `detail`, its `non_field_errors`, its per-field
// errors, and our own fallback), and the order between them is the whole
// behaviour. Two of the defects fixed in #501 were orderings — a hint that
// could never surface because `non_field_errors` outranked it, and a 404 body
// that suppressed the only sentence saying what to do. Neither changed a
// character of any message; both changed which one was picked.
//
// `src/dev/` had no tests at all, so nothing pinned any of it.

import { afterEach, describe, expect, it, vi } from "vitest";
import type { AxiosError } from "axios";
import axios from "axios";

import { obtainExactToken } from "./exactAuth";

vi.mock("axios", () => ({
  default: { post: vi.fn() },
}));

const post = vi.mocked(axios.post);

/** An axios rejection carrying a server response. */
function responded(status: number, data: unknown): AxiosError {
  return {
    message: `Request failed with status code ${status}`,
    response: { status, data },
  } as unknown as AxiosError;
}

/** An axios rejection with no response at all — DNS, timeout, refused. */
function noResponse(message: string): AxiosError {
  return { message } as unknown as AxiosError;
}

const HINT =
  "EXACT's username is the identity's sub, not the email — check that first.";
const FLAG = "No /api-token-auth/ on this backend.";
const GENERIC_400 = "Login rejected — check the subject and password.";

afterEach(() => {
  vi.resetAllMocks();
});

describe("obtainExactToken", () => {
  it("returns the token when the call succeeds", async () => {
    post.mockResolvedValueOnce({ data: { token: "abc123" } });
    await expect(obtainExactToken("sub-uuid", "pw")).resolves.toBe("abc123");
  });

  describe("chooses the most actionable message available", () => {
    const cases: Array<{
      name: string;
      rejection: AxiosError;
      /** Substrings the message must contain, in no particular order. */
      says: string[];
      /** Substrings it must NOT contain — usually the message it outranks. */
      notSays?: string[];
    }> = [
      {
        // The ordinary wrong-password answer from `ObtainAuthToken`.
        name: "400 with non_field_errors: the server's own words, plus the hint",
        rejection: responded(400, {
          non_field_errors: ["Unable to log in with provided credentials."],
        }),
        says: ["Unable to log in with provided credentials.", HINT],
        notSays: [GENERIC_400],
      },
      {
        // Reachable: HTML `required` blocks "" but not "   ", and DRF trims.
        // Before #501 this per-field error was discarded for a generic line.
        name: "400 with only a per-field error: names the field, as the FORM labels it",
        rejection: responded(400, { username: ["This field may not be blank."] }),
        says: ["Subject: This field may not be blank.", HINT],
        notSays: ["username:", GENERIC_400],
      },
      {
        // The hint talks about the subject, so a message naming `password`
        // would point the reader at the wrong control.
        name: "400 with several field errors: prefers the one the hint is about",
        rejection: responded(400, {
          password: ["This field is required."],
          username: ["This field may not be blank."],
        }),
        says: ["Subject: This field may not be blank."],
        notSays: ["password:"],
      },
      {
        name: "400 with an empty body: the generic line, still with the hint",
        rejection: responded(400, {}),
        says: [GENERIC_400, HINT],
      },
      {
        // `typeof [] === "object"`, so an un-guarded reader would render
        // element 0 as though it were a field called `0`.
        name: "400 with an array body: not read as a field called 0",
        rejection: responded(400, ["gateway down"]),
        says: [GENERIC_400],
        notSays: ["0:", "gateway down"],
      },
      {
        // A field whose value is neither a string nor [string] tells the
        // reader nothing; fall back rather than print `[object Object]`.
        name: "400 with an unreadable field value: falls back",
        rejection: responded(400, { username: { nested: true } }),
        says: [GENERIC_400],
        notSays: ["object Object"],
      },
      {
        name: "401 with a detail: the server's words, plus the hint",
        rejection: responded(401, { detail: "Invalid token." }),
        says: ["Invalid token.", HINT],
      },
      {
        // The point of #501: a 404 is a backend flag, not a typo, and no
        // body it carries beats naming the flag.
        name: "404 with Django's HTML page: names the flag",
        rejection: responded(404, "<html>Page not found</html>"),
        says: [FLAG, "ENABLE_DRF_TOKEN_AUTH"],
      },
      {
        // The ordinary answer from a misrouted proxy. Reading it first
        // restates the status code and suppresses the remedy.
        name: "404 with a DRF detail: still names the flag",
        rejection: responded(404, { detail: "Not found." }),
        says: [FLAG],
        notSays: ["Not found."],
      },
      {
        // A gateway that JSON-ifies its 404s is exactly the "wrong proxy
        // target" case the flag message calls out.
        name: "404 with an arbitrary JSON body: still names the flag",
        rejection: responded(404, { error: "upstream unavailable" }),
        says: [FLAG],
        notSays: ["upstream unavailable"],
      },
      {
        // Not a credential problem, so the subject hint would be noise.
        name: "403: the server's words, and no hint",
        rejection: responded(403, { detail: "CSRF Failed" }),
        says: ["CSRF Failed"],
        notSays: [HINT, FLAG],
      },
      {
        name: "500 with no useful body: axios's message, and no hint",
        rejection: responded(500, "<html>Server Error</html>"),
        says: ["Request failed with status code 500"],
        notSays: [HINT],
      },
      {
        // A 500 body is not a login error; naming one of its keys would
        // dress a server fault as something the reader typed wrong.
        name: "500 with a JSON body: does not mine it for a field",
        rejection: responded(500, { trace_id: "7f3a2b", error_code: 9 }),
        says: ["Request failed with status code 500"],
        notSays: ["trace_id"],
      },
      {
        name: "no response at all: axios's message, and no hint",
        rejection: noResponse("Network Error"),
        says: ["Network Error"],
        notSays: [HINT, FLAG],
      },
      {
        name: "a timeout: says so",
        rejection: noResponse("timeout of 10000ms exceeded"),
        says: ["timeout of 10000ms exceeded"],
      },
    ];

    for (const { name, rejection, says, notSays } of cases) {
      it(name, async () => {
        post.mockRejectedValueOnce(rejection);
        const error = await obtainExactToken("sub-uuid", "pw").then(
          () => {
            throw new Error("expected a rejection");
          },
          (e: Error) => e,
        );
        for (const fragment of says) {
          expect(error.message).toContain(fragment);
        }
        for (const fragment of notSays ?? []) {
          expect(error.message).not.toContain(fragment);
        }
      });
    }
  });

  it("separates the server's words from our hint", async () => {
    // `detail` comes from the server and does not reliably end in
    // punctuation, so the two are joined rather than concatenated.
    post.mockRejectedValueOnce(
      responded(400, { non_field_errors: ["Unable to log in"] }),
    );
    await expect(obtainExactToken("sub-uuid", "pw")).rejects.toThrow(
      `Unable to log in · ${HINT}`,
    );
  });

  it("posts the subject as `username`, which is what the wire calls it", async () => {
    // The form's label changed; the field name on the request did not.
    post.mockResolvedValueOnce({ data: { token: "t" } });
    await obtainExactToken("4d171781-3a39-4358-9d16-e7e078860f7f", "pw");
    expect(post).toHaveBeenCalledWith(
      expect.stringContaining("/api-token-auth/"),
      { username: "4d171781-3a39-4358-9d16-e7e078860f7f", password: "pw" },
      expect.objectContaining({ timeout: expect.any(Number) }),
    );
  });
});
