// The pre-existing-conditions answer, which is an attestation rather than a
// field (#5665).
//
// PROMOP keeps this out of the shared field descriptor on purpose: the answer
// has its own endpoint, its own audit rows and a snapshot, because a generic
// PATCH could overwrite an answer another tab had just made. The trial page
// therefore cannot use the inline editor; it asks the reader explicitly, sends
// the snapshot it read, and shows the 409 when the answer moved underneath.

import { useEffect, useState } from "react";

import { Dialog } from "./Dialog";
import { optionsOf } from "./writable";
import type { WritableFieldEntry } from "./writable";
import { AnswerChanged } from "./state";
import type {
  ExplicitAnswerAdapter,
  PreExistingConditionsAnswer,
  PreExistingConditionsWrite,
} from "./state";

type Mode = "none" | "categories";

export interface ExplicitAnswerDialogProps {
  /** The row's label, for the dialog's accessible name. */
  label: string;
  /** The descriptor entry, which carries the category vocabulary. */
  entry: WritableFieldEntry;
  answers: ExplicitAnswerAdapter;
  onClose: () => void;
  /** Called after a write lands, so the page re-reads the trial. */
  onAnswered?: () => void;
}

/** The sentence to show for a refusal, without inventing one. */
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

function modeOf(answer: PreExistingConditionsAnswer): Mode | null {
  if (answer.no_pre_existing_conditions === true) return "none";
  if ((answer.preexisting_conditions?.length ?? 0) > 0) return "categories";
  return null;
}

export function ExplicitAnswerDialog({
  label,
  entry,
  answers,
  onClose,
  onAnswered,
}: ExplicitAnswerDialogProps) {
  const options = optionsOf(entry);
  const [answer, setAnswer] = useState<PreExistingConditionsAnswer | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      const current = await answers.read();
      setAnswer(current);
      setMode(modeOf(current));
      setSelected(current.preexisting_conditions ?? []);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // `answers` is a stable adapter from the host; re-reading on every render
    // would race the reader's own choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = async (write: PreExistingConditionsWrite) => {
    setSaving(true);
    setError(null);
    try {
      await answers.write(write);
      onAnswered?.();
      onClose();
    } catch (failure) {
      if (failure instanceof AnswerChanged) {
        setStale(true);
        return;
      }
      setError(messageOf(failure));
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    if (!answer || mode === null) return;
    const expected_flag = answer.no_pre_existing_conditions;
    const expected_categories = answer.preexisting_conditions;
    if (mode === "none") {
      void send({ action: "confirm_none", expected_flag, expected_categories });
      return;
    }
    if (selected.length === 0) {
      setError("Select at least one condition, or answer that you have none.");
      return;
    }
    void send({
      action: "set_categories",
      categories: [...new Set(selected)],
      expected_flag,
      expected_categories,
    });
  };

  const withdraw = () => {
    if (!answer) return;
    void send({
      action: "withdraw",
      expected_flag: answer.no_pre_existing_conditions,
      expected_categories: answer.preexisting_conditions,
    });
  };

  const answered =
    answer != null
    && (answer.no_pre_existing_conditions !== null
      || (answer.preexisting_conditions?.length ?? 0) > 0);

  return (
    <Dialog label={label} title={label} onClose={onClose}>
      <p className="exact-subform__note">
        This is your own answer, not a value read from your records. Trials
        that exclude a condition use exactly what you say here.
      </p>

      {loading ? (
        <p className="exact-subform__note">Loading your answer…</p>
      ) : (
        <fieldset className="exact-answer__choices">
          <legend className="exact-answer__legend">Your pre-existing conditions</legend>
          <label className="exact-answer__choice">
            <input
              type="radio"
              name="pre-existing-conditions"
              checked={mode === "none"}
              onChange={() => setMode("none")}
              disabled={saving}
            />
            I have none of these conditions
          </label>
          <label className="exact-answer__choice">
            <input
              type="radio"
              name="pre-existing-conditions"
              checked={mode === "categories"}
              onChange={() => setMode("categories")}
              disabled={saving}
            />
            I have one or more of these conditions
          </label>

          {mode === "categories" ? (
            <ul className="exact-answer__list">
              {options.map((option) => (
                <li key={option.value}>
                  <label className="exact-answer__choice">
                    <input
                      type="checkbox"
                      checked={selected.includes(option.value)}
                      disabled={saving}
                      onChange={(event) =>
                        setSelected((current) =>
                          event.target.checked
                            ? [...current, option.value]
                            : current.filter((value) => value !== option.value),
                        )
                      }
                    />
                    {option.label}
                  </label>
                </li>
              ))}
            </ul>
          ) : null}
        </fieldset>
      )}

      {stale ? (
        <p className="exact-elig__error" role="alert">
          Your answer changed somewhere else. Nothing was saved — review the
          answer and try again.
          <button type="button" className="exact-answer__link" onClick={() => void load()}>
            Reload the answer
          </button>
        </p>
      ) : null}
      {error ? (
        <p className="exact-elig__error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="exact-answer__actions">
        <button
          type="button"
          onClick={save}
          disabled={saving || loading || mode === null}
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onClose} disabled={saving}>
          Cancel
        </button>
        {answered ? (
          <button type="button" onClick={withdraw} disabled={saving}>
            Withdraw my answer
          </button>
        ) : null}
      </div>
    </Dialog>
  );
}
