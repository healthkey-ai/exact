// The dedicated editor for an inverted row (#5667), on its own.
//
// Three things are worth pinning here, and none of them is the markup: the
// words the dialog asks in, which value it seeds from, and that the value it
// writes is the canonical one with no inversion left in this component.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NegatedBooleanDialog } from "./NegatedBooleanDialog";
import type { RowEditing } from "./TrialDetailPage";
import type { TrialDetailField } from "./types";

const field: TrialDetailField = {
  name: "noHivRequired",
  label: "No Hiv",
  type: "boolean",
  value: true,
  ureadonly: false,
  ufield: "noHivStatus",
  upatientField: "no_hiv_status",
  uvalue: true,
  utype: "boolean",
};

const source = {
  upatientField: "hiv_status",
  type: "boolean",
  available: true,
  value: null as unknown,
};

function editing(over: Partial<RowEditing> = {}): RowEditing {
  return { save: vi.fn(), outstanding: {}, failed: {}, ...over };
}

function open(edit: RowEditing) {
  return render(
    <NegatedBooleanDialog
      field={field}
      canonical="hiv_status"
      positive="HIV Positive"
      negative="No HIV"
      source={source}
      editing={edit}
      onClose={() => {}}
    />,
  );
}

describe("what it asks", () => {
  it("asks in the canonical words, never in Yes/No", () => {
    open(editing());

    expect(screen.getByLabelText("HIV Positive")).toBeInTheDocument();
    expect(screen.getByLabelText("No HIV")).toBeInTheDocument();
    expect(screen.getByLabelText("Not answered")).toBeInTheDocument();
    expect(screen.queryByLabelText("Yes")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("No")).not.toBeInTheDocument();
  });
});

describe("where it seeds from", () => {
  it("uses the record when nothing is in flight", () => {
    open(editing());

    expect(screen.getByLabelText("Not answered")).toBeChecked();
  });

  it("uses an in-flight value before the record", () => {
    open(editing({ outstanding: { hiv_status: true } }));

    expect(screen.getByLabelText("HIV Positive")).toBeChecked();
  });

  it("uses a refused value before everything else", () => {
    // The reader is about to retry; the thing they want in the box is what
    // they chose, not what the record still says.
    open(editing({
      outstanding: { hiv_status: true },
      failed: { hiv_status: false },
    }));

    expect(screen.getByLabelText("No HIV")).toBeChecked();
  });
});

describe("what it writes", () => {
  it("saves the positive answer as true, not the row's inverted value", async () => {
    const edit = editing();
    const user = userEvent.setup();
    open(edit);

    await user.click(screen.getByLabelText("HIV Positive"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(edit.save).toHaveBeenCalledWith("hiv_status", true);
  });

  it("saves the negative answer as false", async () => {
    const edit = editing();
    const user = userEvent.setup();
    open(edit);

    await user.click(screen.getByLabelText("No HIV"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(edit.save).toHaveBeenCalledWith("hiv_status", false);
  });

  it("saves an explicit null for Not answered", async () => {
    const edit = editing();
    const user = userEvent.setup();
    open(edit);

    await user.click(screen.getByLabelText("Not answered"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(edit.save).toHaveBeenCalledWith("hiv_status", null);
  });

  it("writes nothing on Cancel", async () => {
    const edit = editing();
    const user = userEvent.setup();
    open(edit);

    await user.click(screen.getByLabelText("No HIV"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(edit.save).not.toHaveBeenCalled();
  });
});
