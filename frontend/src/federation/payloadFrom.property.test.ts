// What an untouched Save is allowed to do: nothing.
//
// This harness exists because three review rounds each found a different
// spelling of one bug in `payloadFrom`, and each fix was written against the
// cases someone happened to think of. The rule below is one line, and it
// fails on all four of those findings in about a second:
//
//   1. record "" + untouched Save -> null           (destroys a FLIPI score of 0)
//   2. record null + untouched Save -> []           (the same, in the branch
//                                                    the second fix skipped)
//   3. record [] over a text column -> []           (400s the whole batch)
//
// Three of the four. It does NOT see the fourth, which was about WHICH value
// the comparison uses
// (the page shows a refused or in-flight value in front of the record's, and
// the rule was reading that). That one is wiring, not arithmetic, and is
// pinned through the page by "takes back a refused edit without destroying
// what the record holds" in `FieldEdit.test.tsx`.
//
// The generator deliberately carries the shapes that broke it: "", " ", ",",
// [], [""], null and undefined, alongside ordinary values.

import { describe, expect, it } from "vitest";

import { payloadFrom } from "./FieldEdit";
import { splitJoined } from "./writable";

/** Same seeded LCG as `weightsWizardState.test.ts`, so a run is repeatable.
 *  Every assertion carries the inputs with it — a property that fails over
 *  400 iterations and prints only "expected true to be false" costs more time
 *  than it saves. */
function lcg(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

const CODES = ["age", "stage", "hemoglobin", "nodalAreas", "ldh"];

/** Every shape a record's value has been observed to arrive in, including the
 *  four that caused the findings above. */
const RECORD_VALUES: unknown[] = [
  null,
  undefined,
  "",
  " ",
  ",",
  " , ",
  "age",
  "age,stage",
  "age, stage",
  // Out of the option list's order, and with a repeat. Both are here because
  // a set comparison in `changedFrom` would be wrong and the harness could
  // not tell: with every fixture in canonical order, ordered and unordered
  // agree on every input, and the mutation survived.
  "stage,age",
  "age,age",
  "inv(3)(q21,q26)",
  [],
  [""],
  ["age"],
  ["age", "stage"],
  ["stage", "age"],
  ["age", ""],
];

function pick<T>(rand: () => number, from: readonly T[]): T {
  return from[Math.floor(rand() * from.length)]!;
}

describe("payloadFrom, over generated records and drafts", () => {
  it("never changes the record when the draft still says what the record says", () => {
    // THE invariant: a draft equal to `splitJoined(recordValue)` is a reader
    // whose answer matches the record, so saving must change nothing.
    //
    // Note what this does NOT say. It is not "the control was untouched" —
    // `draftFrom` seeds from `value`, which carries a refused or in-flight
    // value in front of the record's, so an untouched control and a draft
    // matching the record are different sets. Conflating them is what
    // produced the fourth finding; the rule under test is about the answer,
    // and the page-level test covers the other one.
    for (const joined of [true, false]) {
      for (const recordValue of RECORD_VALUES) {
        const untouched = splitJoined(recordValue);
        const payload = payloadFrom(untouched, "multiselect", joined, recordValue);
        const where = JSON.stringify({ recordValue, untouched, joined });

        // Already in the column's shape means byte for byte, not merely
        // equivalent: rebuilding drops an empty element the record held.
        if (recordValue != null && Array.isArray(recordValue) === !joined) {
          expect(payload, where).toBe(recordValue);
        }
        // "Unchanged" is about the answer, not its spelling: what comes back
        // must carry the same values, in the shape this column takes.
        expect(splitJoined(payload), where).toEqual(untouched);
        if (joined) {
          expect(payload === null || typeof payload === "string", where).toBe(true);
        } else {
          expect(payload === null || Array.isArray(payload), where).toBe(true);
        }
        // An empty record must not become an empty ANSWER, and vice versa.
        // `null` and `""` derive different FLIPI scores at the far end.
        expect(payload == null, where).toBe(recordValue == null);
      }
    }
  });

  it("never sends an array to a comma-joined column, whatever the record held", () => {
    // Finding 3: PROMOP generates a plain CharField for these and answers a
    // list with "Not a valid string." — before any validator runs, and the
    // 400 fails every other field written in the same breath.
    const rand = lcg(20260926);
    for (let i = 0; i < 400; i += 1) {
      const recordValue = pick(rand, RECORD_VALUES);
      const draft = CODES.filter(() => rand() < 0.4);
      const payload = payloadFrom(draft, "multiselect", true, recordValue);
      const where = JSON.stringify({ recordValue, draft });

      expect(Array.isArray(payload), where).toBe(false);
      expect(payload === null || typeof payload === "string", where).toBe(true);
    }
  });

  it("sends what the reader chose whenever the reader chose something else", () => {
    const rand = lcg(4711);
    for (let i = 0; i < 400; i += 1) {
      const recordValue = pick(rand, RECORD_VALUES);
      const draft = CODES.filter(() => rand() < 0.4);
      if (!draft.length) continue;
      const held = splitJoined(recordValue);
      if (held.length === draft.length && held.every((p, at) => p === draft[at])) continue;

      for (const joined of [true, false]) {
        const payload = payloadFrom(draft, "multiselect", joined, recordValue);
        expect(splitJoined(payload), JSON.stringify({ recordValue, draft, joined })).toEqual(
          draft,
        );
      }
    }
  });

  it("clears a joined column, and empties a list column, on a deliberate clear", () => {
    // Deselecting everything from a non-empty record IS an answer. The two
    // column shapes spell it differently, which is the whole reason `joined`
    // is threaded this far down.
    for (const recordValue of ["age", "age,stage", ["age"]]) {
      expect(payloadFrom([], "multiselect", true, recordValue)).toBeNull();
      expect(payloadFrom([], "multiselect", false, recordValue)).toEqual([]);
    }
  });
});
