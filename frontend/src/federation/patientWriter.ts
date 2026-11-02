// The queue behind inline editing.
//
// A reader filling in three gaps in a row should cost one request and one
// re-derivation, not three of each — every write into the patient record
// re-derives the projection and rescores the match. So edits collect for a
// beat, go out together, and a new edit made while one is on the wire waits
// for the next batch rather than racing it.
//
// Shaped after `PreferenceWriter`, which does the same job for the saved
// filters, and deliberately NOT the same class: that one replaces a whole
// value, last-write-wins. Here two edits to DIFFERENT fields must both land,
// which is a merge, and merging is the one thing it must not do to filters —
// a cleared filter would come back.

import type { TrialStateAdapter, WriteOutcome } from "./state";

/** How long an edit waits for company.
 *
 *  Short, because the reader has already pressed Save and the row is showing
 *  their value on trust until the record answers. Long enough that filling in
 *  two fields in one breath is one request — which is the common shape of
 *  this page, where a trial asks for a haemoglobin and a platelet count in
 *  adjacent rows. */
export const PATIENT_WRITE_DEBOUNCE_MS = 250;

export interface PatientWriterOptions {
  debounceMs?: number;
  /** Called once per field when a batch comes back, whatever it says.
   *
   *  `saved`, `differs` and `unconfirmed` all arrive here: the difference
   *  between them is what the caller should SAY, not whether it is done. */
  onSettled?: (field: string, outcome: WriteOutcome) => void;
  /** The batch was refused — 403, 404, a 400 from the serializer. Reported
   *  with every field in it, because the caller cannot tell from a rejection
   *  which of them the server objected to. */
  onError?: (fields: string[], error: unknown) => void;
  /** Once per batch, after its fields have been reported. Separate from
   *  `onSettled` because what hangs off it is the re-read: the match moves
   *  when a REQUEST lands, so one re-read per request, not one per value. */
  onBatchSettled?: () => void;
}

type Writer = NonNullable<TrialStateAdapter["setPatientFields"]>;

export class PatientFieldWriter {
  private readonly write: Writer;
  private readonly debounceMs: number;
  private readonly onSettled: (field: string, outcome: WriteOutcome) => void;
  private readonly onError: (fields: string[], error: unknown) => void;
  private readonly onBatchSettled: () => void;

  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Edits waiting for the timer, and edits waiting for the wire to clear.
   *  One map, because an edit that arrives during a flight simply joins the
   *  next batch — there is no third state for it to be in. */
  private queued: Record<string, unknown> = {};
  private inFlight: Promise<void> | null = null;

  constructor(write: Writer, opts: PatientWriterOptions = {}) {
    this.write = write;
    this.debounceMs = opts.debounceMs ?? PATIENT_WRITE_DEBOUNCE_MS;
    this.onSettled = opts.onSettled ?? (() => {});
    this.onBatchSettled = opts.onBatchSettled ?? (() => {});
    this.onError =
      opts.onError ??
      ((fields, error) => {
        // Not silence by default: a save the reader watched disappear with
        // nothing said anywhere is the failure this whole phase exists to
        // avoid.
        console.warn("[exact] patient fields could not be written", fields, error);
      });
  }

  /** Write this field, eventually.
   *
   *  A second edit to the same field replaces the first: only the last value
   *  the reader chose is theirs, and sending the intermediate one would make
   *  the record hold a value they had already moved on from. */
  save(field: string, value: unknown): void {
    this.queued[field] = value;
    this.arm();
  }

  /** Send what is waiting now, without waiting for the timer. */
  flush(): void {
    this.cancelTimer();
    this.drain();
  }

  /** Resolves when nothing is on the wire. Does NOT send what is merely
   *  waiting for the timer — call `flush()` first if that is what is meant,
   *  which is what an unmount wants. */
  async settled(): Promise<void> {
    while (this.inFlight) await this.inFlight;
  }

  /** Fields with an edit that has not come back yet, on the wire or waiting
   *  for it. What the page paints its optimistic values from. */
  get outstanding(): string[] {
    return [...new Set([...Object.keys(this.queued), ...Object.keys(this.sent)])];
  }

  private sent: Record<string, unknown> = {};

  private arm(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.drain();
    }, this.debounceMs);
  }

  private cancelTimer(): void {
    if (this.timer === null) return;
    clearTimeout(this.timer);
    this.timer = null;
  }

  private drain(): void {
    // One request at a time. Two PATCHes to the same record racing each other
    // is not a performance question: each one re-derives the projection from
    // the facts it just wrote, so the loser's derivation can land last and
    // describe a record that no longer exists.
    if (this.inFlight) return;
    const batch = this.queued;
    if (Object.keys(batch).length === 0) return;
    this.queued = {};
    this.sent = batch;

    // `Promise.resolve().then` rather than calling `write` bare: a transport
    // that throws SYNCHRONOUSLY would otherwise escape before `inFlight` is
    // assigned, stranding the batch and leaving the queue believing nothing
    // is on the wire. Reachable — a host that drops its adapter while an edit
    // is queued leaves the call dereferencing a method that is gone.
    this.inFlight = Promise.resolve()
      .then(() => this.write(batch))
      .then((outcomes) => {
        for (const field of Object.keys(batch)) {
          // A field the answer does not mention is `unconfirmed` rather than
          // missing: the caller is owed a verdict for everything it sent.
          //
          // Guarded, because `.catch` is chained after this: a callback that
          // threw while REPORTING a success would be caught below and the
          // whole batch reported as refused — the row saying "couldn't save"
          // over a value the record does hold, which is the inverse of the
          // failure this queue exists to prevent.
          try {
            this.onSettled(field, outcomes?.[field] ?? { status: "unconfirmed" });
          } catch (error: unknown) {
            console.warn("[exact] a write outcome could not be reported", field, error);
          }
        }
      })
      .catch((error: unknown) => {
        try {
          this.onError(Object.keys(batch), error);
        } catch (reportingError: unknown) {
          console.warn("[exact] a write failure could not be reported", reportingError);
        }
      })
      .finally(() => {
        // Guarded for a harder reason than the others: this runs BEFORE the
        // queue is unlocked, so a throw here would leave `inFlight` set for
        // ever — every later edit silently dropped and "Saving…" never
        // retired. In this codebase it is three `invalidateQueries` calls.
        try {
          this.onBatchSettled();
        } catch (error: unknown) {
          console.warn("[exact] the post-write re-read could not be started", error);
        }
        this.sent = {};
        this.inFlight = null;
        // Whatever arrived while this was in flight goes out now, rather than
        // waiting for another edit to arm the timer — otherwise a reader who
        // makes their last edit during a flight watches it sit there.
        this.drain();
      });
  }
}
