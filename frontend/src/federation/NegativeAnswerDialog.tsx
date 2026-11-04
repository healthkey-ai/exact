// Explicit confirmation of a derived negative state (#5666).
//
// Like the pre-existing-conditions answer, this is an attestation with its own
// endpoint and snapshot, not a field PATCH. The dialog asks for the negative
// only: recording an active infection or a second malignancy belongs in the
// clinical record, where the source derivation reads it.

import { useEffect, useState } from "react";

import { Dialog } from "./Dialog";
import { AnswerChanged } from "./state";
import type {
  NegativeAnswer,
  NegativeAnswerAdapter,
  NegativeAnswerField,
} from "./state";
import type { WritableFieldEntry } from "./writable";

function messageOf(error: unknown): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data && typeof data === "object") {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail !== "") return detail;
    for (const value of Object.values(data)) {
      const first = Array.isArray(value) ? value[0] : value;
      if (typeof first === "string" && first !== "") return first;
    }
  }
  return "Couldn't save that. Your answer is not in the record.";
}

function labelOf(entry: WritableFieldEntry, key: string, fallback: string): string {
  const labels = entry.action_labels;
  if (labels && typeof labels === "object") {
    const value = (labels as Record<string, unknown>)[key];
    if (typeof value === "string" && value !== "") return value;
  }
  return fallback;
}

export interface NegativeAnswerDialogProps {
  label: string;
  field: NegativeAnswerField;
  entry: WritableFieldEntry;
  answers: NegativeAnswerAdapter;
  onClose: () => void;
  onAnswered?: (answer: NegativeAnswer) => void;
}

export function NegativeAnswerDialog({
  label,
  field,
  entry,
  answers,
  onClose,
  onAnswered,
}: NegativeAnswerDialogProps) {
  const [answer, setAnswer] = useState<NegativeAnswer | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      setAnswer(await answers.read(field));
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // The adapter is stable; re-reading on every render would race the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = async (action: "confirm_negative" | "withdraw") => {
    if (!answer) return;
    setSaving(true);
    setError(null);
    try {
      const next = await answers.write({
        field,
        action,
        expected_value: answer.value,
        expected_source: answer.source,
      });
      onAnswered?.(next);
      onClose();
    } catch (failure) {
      if (failure instanceof AnswerChanged) {
        setStale(true);
      } else {
        setError(messageOf(failure));
      }
    } finally {
      setSaving(false);
    }
  };

  const confirmed = answer?.confirmed === true;
  const confirmLabel = labelOf(
    entry,
    "confirm_negative",
    `Confirm ${label.toLowerCase()}`,
  );

  return (
    <Dialog label={label} title={label} onClose={onClose}>
      <p className="exact-subform__note">
        This is your own confirmation, not a value read from your records.
      </p>
      {loading ? (
        <p className="exact-subform__note">Loading your answer…</p>
      ) : stale ? (
        <p className="exact-elig__error" role="alert">
          Your answer changed somewhere else. Nothing was saved — review the
          answer and try again.
          <button type="button" className="exact-answer__link" onClick={() => void load()}>
            Reload the answer
          </button>
        </p>
      ) : (
        <>
          <p className="exact-answer__legend">
            {confirmed
              ? "You confirmed this. Withdrawing it returns the value to your records."
              : "Confirm only if this is true for you."}
          </p>
          <div className="exact-answer__actions">
            {confirmed ? (
              <button type="button" onClick={() => void send("withdraw")} disabled={saving}>
                {saving ? "Saving…" : "Withdraw confirmation"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void send("confirm_negative")}
                disabled={saving}
              >
                {saving ? "Saving…" : confirmLabel}
              </button>
            )}
            <button type="button" onClick={onClose} disabled={saving}>
              Cancel
            </button>
          </div>
        </>
      )}
      {error ? (
        <p className="exact-elig__error" role="alert">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}
