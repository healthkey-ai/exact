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

import { IdentityChanged, sameIdentity } from "./identityFingerprint";
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
   *  which of them the server objected to.
   *
   *  Also how a STRANDED write is reported: one that was never sent because
   *  the signed-in account changed while it waited. The error is an
   *  `IdentityChanged`, so a caller that wants to word it differently can;
   *  one that does not gets "could not be written", which is true. This is
   *  not optional politeness — `onError` is what retires a field from
   *  "Saving…", and a stranded field that went unreported would sit there
   *  for the life of the page. */
  onError?: (fields: string[], error: unknown) => void;
  /** Once per batch, after its fields have been reported. Separate from
   *  `onSettled` because what hangs off it is the re-read: the match moves
   *  when a REQUEST lands, so one re-read per request, not one per value. */
  onBatchSettled?: () => void;
  /** Who is signed in, RIGHT NOW, as a fingerprint (`identityFingerprint.ts`).
   *
   *  Read twice: when an edit is enqueued, and again when a batch goes out.
   *  A payload whose two readings disagree is not sent — EXACT keys the row
   *  on the token, so sending it would write one reader's value into
   *  another's record, which is the whole of #583.
   *
   *  Synchronous on purpose. `getToken` is async, and awaiting it inside
   *  `save()` would let the reader's keystroke land after the batch it
   *  belongs to. The bridge records the fingerprint of each token it hands
   *  out and this reads the last one.
   *
   *  Omitted means no guard, and every caller behaves exactly as it did.
   *  That is what lets this be wired one call site at a time, and it is also
   *  the honest default: a queue with no way to learn who is signed in
   *  cannot tell a stranger from a refresh. */
  identity?: () => string | undefined;
}

type Writer = NonNullable<TrialStateAdapter["setPatientFields"]>;

/** Own property, not `in`.
 *
 *  `in` walks the prototype chain, so `"constructor"` is a member of every
 *  object literal. An error naming it would pass the intersection below,
 *  leave the whole batch to re-queue, and `finally` re-drains — the
 *  identical request for ever. Unreachable from a DRF field name, but the
 *  point of that intersection is to make termination a property of the code
 *  rather than of who is calling it.
 */
function has(bag: Record<string, unknown>, field: string): boolean {
  return Object.prototype.hasOwnProperty.call(bag, field);
}

/** The fields an error names, when it names any.
 *
 *  Duck-typed rather than `instanceof`: the error crosses a module boundary
 *  a host may have bundled twice, and a class identity check that fails
 *  silently would put this back to losing the batch. A host adapter that
 *  throws something else gets the old behaviour, which is the safe default
 *  — everything unsaved is reported unsaved.
 */
function namedFields(error: unknown, sent: Record<string, unknown>): string[] {
  const fields = (error as { fields?: unknown })?.fields;
  if (!Array.isArray(fields)) return [];
  // Intersected with what was actually sent, and that is what makes the
  // retry terminate rather than a promise that it will. An error naming
  // something outside the batch leaves `rest` equal to the whole batch, and
  // `finally` re-drains, so the identical request goes out for ever while
  // the fields it carries sit in "Saving…". The PROMOP adapter already
  // filters, but this accepts a duck-typed error from any host adapter, so
  // the guarantee has to live here.
  return fields.filter(
    (field): field is string => typeof field === "string" && has(sent, field),
  );
}

export class PatientFieldWriter {
  private readonly write: Writer;
  private readonly debounceMs: number;
  private readonly onSettled: (field: string, outcome: WriteOutcome) => void;
  private readonly onError: (fields: string[], error: unknown) => void;
  private readonly onBatchSettled: () => void;
  private readonly identity: () => string | undefined;

  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Edits waiting for the timer, and edits waiting for the wire to clear.
   *  One map, because an edit that arrives during a flight simply joins the
   *  next batch — there is no third state for it to be in. */
  private queued: Record<string, unknown> = {};
  /** Who was signed in when each queued edit was made. Keyed per FIELD, not
   *  per batch: a batch can carry an edit from before an account change and
   *  one from after it, and only the first is a stranger's. */
  private queuedBy: Record<string, string | undefined> = {};
  private inFlight: Promise<void> | null = null;

  constructor(write: Writer, opts: PatientWriterOptions = {}) {
    this.write = write;
    this.debounceMs = opts.debounceMs ?? PATIENT_WRITE_DEBOUNCE_MS;
    this.onSettled = opts.onSettled ?? (() => {});
    this.onBatchSettled = opts.onBatchSettled ?? (() => {});
    this.identity = opts.identity ?? (() => undefined);
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
    // Captured HERE, not at flush: this is the moment the reader made the
    // edit, and it is the only moment at which who they are is not in doubt.
    // Replaced along with the value, because a second edit to the same field
    // is a new statement by whoever is signed in now.
    this.queuedBy[field] = this.identity();
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
    const taken = this.queued;
    if (Object.keys(taken).length === 0) return;
    this.queued = {};

    // WHO THIS BATCH IS FOR, decided one field at a time. `sameIdentity`
    // lets a rotated token and an unknown identity through and stops a known
    // stranger — see `identityFingerprint.ts` for why those are not the same
    // question. A field that fails it is STRANDED: never sent, reported, and
    // gone. It is not re-queued, because the credential that could have
    // carried it is the thing that went away.
    const now = this.identity();
    const batch: Record<string, unknown> = {};
    const batchBy: Record<string, string | undefined> = {};
    const stranded: string[] = [];
    for (const field of Object.keys(taken)) {
      batchBy[field] = this.queuedBy[field];
      delete this.queuedBy[field];
      if (sameIdentity(batchBy[field], now)) batch[field] = taken[field];
      else stranded.push(field);
    }
    if (stranded.length > 0) {
      try {
        this.onError(stranded, new IdentityChanged(stranded));
      } catch (error: unknown) {
        console.warn("[exact] a stranded write could not be reported", stranded, error);
      }
    }
    // Nothing survived, so nothing reached the server — and `onBatchSettled`
    // is the re-read hook, whose whole premise is that a request landed.
    // Firing it here would re-read on behalf of a batch that never left.
    if (Object.keys(batch).length === 0) return;
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
        // A REJECTED BATCH MUST NOT COST THE READER AN EDIT THE SERVER DID
        // NOT OBJECT TO. DRF rejects the whole request on a 400, so nothing
        // was written and everything here is unsaved — that part was always
        // reported correctly. What it cost was the rest of the batch: three
        // edits made in one breath, one of them a value the vocabulary does
        // not recognise, and all three gone with no way to tell which.
        //
        // So when the server NAMED the fields it objected to, only those are
        // failed and the others go back on the queue to be sent again. The
        // retry strictly shrinks the batch, so a second refusal naming
        // something else still terminates.
        //
        // Reporting only the named ones without re-queueing the rest would
        // be worse than today: `onError` is what retires a field from
        // "Saving…", so anything left unreported stays there for ever.
        //
        // "Strictly shrinks" holds because `namedFields` intersects with the
        // batch, so a non-empty answer always removes at least one field.
        // Without that it is not a guarantee but a hope, and the failure is
        // an infinite retry of the identical request.
        const refused = namedFields(error, batch);
        const rest = refused.length > 0
          ? Object.keys(batch).filter((field) => !refused.includes(field))
          : [];
        const failed = refused.length > 0 ? refused : Object.keys(batch);
        for (const field of rest) {
          if (has(this.queued, field)) {
            // SUPERSEDED. The reader edited this field again while the batch
            // was on the wire, so the newer value is already queued and the
            // one being carried back is stale. Re-queueing would MERGE the
            // two — and the caller counts SAVES, not batches, so two saves
            // collapsing into one answer leaves its counter above zero for
            // ever and the row stuck on "Saving…" over a value the record
            // does hold. That is the failure this whole change exists to
            // prevent, one level up, and it is why the attempt that just
            // ended is reported rather than silently dropped: `onSettled`
            // decrements the count and returns early when a newer edit is
            // still owed, which is exactly this case.
            try {
              this.onSettled(field, { status: "unconfirmed" });
            } catch (reportingError: unknown) {
              console.warn("[exact] a superseded write could not be reported", reportingError);
            }
            continue;
          }
          this.queued[field] = batch[field];
          // AND WHO IT WAS FOR. Without this the re-queued field comes back
          // with no owner, `sameIdentity` reads "unknown" and waves it
          // through — so the one write that escapes the guard is the retry,
          // which is the path a reader never sees. Restored to the value the
          // batch carried, not to `this.identity()`, which is whoever is
          // signed in NOW and may be the stranger.
          this.queuedBy[field] = batchBy[field];
        }
        try {
          this.onError(failed, error);
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
