// The dedicated editor for an explicit-answer row.
//
// Pre-existing conditions and the two derived negatives are attestations, not
// scalar columns: PROMOP owns them behind dedicated endpoints with snapshot
// and concurrency semantics, and a record PATCH would overwrite an answer it
// does not own. The descriptor names the endpoint, the actions and the labels;
// this dialog only collects the answer and hands it to the host adapter.

import { useState } from "react";

import { Dialog } from "./Dialog";
import type { ExplicitAnswerEditor } from "./editorResolver";
import type { RowEditing } from "./TrialDetailPage";

export interface ExplicitAnswerDialogProps {
  label: string;
  editor: ExplicitAnswerEditor;
  editing: RowEditing;
  onClose: () => void;
}

const FALLBACK_LABELS: Record<string, string> = {
  confirm_none: "No pre-existing conditions",
  confirm_negative: "Confirm the negative answer",
  set_categories: "Save answer",
  withdraw: "Withdraw answer",
};

export function ExplicitAnswerDialog({
  label,
  editor,
  editing,
  onClose,
}: ExplicitAnswerDialogProps) {
  const adapter = editing.explicitAnswers;
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!adapter) {
    return (
      <Dialog label={`${label}: your answer`} title={label} onClose={onClose}>
        <p className="exact-subform__note">
          This answer cannot be recorded in this view.
        </p>
        <button type="button" className="exact-elig__cancel" onClick={onClose}>
          Close
        </button>
      </Dialog>
    );
  }

  const toggle = (value: string) => {
    setSelected((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
  };

  const submit = async (action: string) => {
    setBusy(true);
    setError("");
    try {
      const payload =
        action === "set_categories" ? { categories: selected } : undefined;
      await adapter.answer(editor.field, action, payload);
      editing.onExplicitAnswerChanged?.();
      onClose();
    } catch (reason) {
      setError(
        reason instanceof Error && reason.message
          ? reason.message
          : "Couldn't save that. Try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  const setCategories = editor.actions.includes("set_categories");
  const others = editor.actions.filter((action) => action !== "set_categories");

  return (
    <Dialog
      label={`${label}: your answer`}
      title={label}
      onClose={onClose}
    >
      <p className="exact-subform__note">
        This answer is recorded as your attestation; it is not a plain field
        edit.
      </p>
      {setCategories ? (
        <>
          <p className="exact-elig__colhdr">Select the conditions that apply</p>
          <fieldset className="exact-subform__list">
            {editor.options.map((option) => (
              <label key={option.value} className="exact-subform__row">
                <input
                  type="checkbox"
                  checked={selected.includes(option.value)}
                  disabled={busy}
                  onChange={() => toggle(option.value)}
                />
                <span className="exact-subform__label">{option.label}</span>
              </label>
            ))}
          </fieldset>
        </>
      ) : null}
      {error ? (
        <p className="exact-elig__error" role="alert">
          {error}
        </p>
      ) : null}
      <div>
        {setCategories ? (
          <button
            type="button"
            className="exact-elig__save"
            disabled={busy || selected.length === 0}
            onClick={() => void submit("set_categories")}
          >
            {editor.actionLabels.set_categories
              ?? FALLBACK_LABELS.set_categories}
          </button>
        ) : null}
        {others.map((action) => (
          <button
            key={action}
            type="button"
            className={action === "withdraw" ? "exact-elig__cancel" : "exact-elig__save"}
            disabled={busy}
            onClick={() => void submit(action)}
          >
            {editor.actionLabels[action] ?? FALLBACK_LABELS[action] ?? action}
          </button>
        ))}
        <button
          type="button"
          className="exact-elig__cancel"
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
    </Dialog>
  );
}
