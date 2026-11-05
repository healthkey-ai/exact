// The genomic findings editor, opened from Trial Details.
//
// `genetic_mutations` is not a scalar patient-record field: each entry is a
// finding with a gene, a variant, an origin and an interpretation, and it is
// written through the dedicated genomics endpoint. The generic `SubformDialog`
// refuses it on purpose (see `writable.ts`), so this is the only in-page way
// to answer "Mutation Genes" / "Mutation Interpretations" without sending the
// reader to another screen.
import { useEffect, useState } from "react";

import { Dialog } from "./Dialog";
import type { RowEditing } from "./TrialDetailPage";
import type { GenomicCatalogMarker, GenomicFinding } from "./state";

const ORIGINS = ["", "Germline", "Somatic", "Unknown"];
const INTERPRETATIONS = [
  "",
  "Pathogenic",
  "Likely pathogenic",
  "VUS",
  "Likely benign",
  "Benign",
  "Uncertain",
];
const STATUSES = ["present", "absent", "indeterminate"];

const label = (value: string | undefined) => value || "—";

function findingTitle(finding: GenomicFinding): string {
  return (
    finding.variant_name ||
    finding.variant ||
    finding.genomic_dna_change ||
    finding.amino_acid_change ||
    "Not recorded"
  );
}

function geneOf(finding: GenomicFinding): string {
  return String(finding.gene || finding.genomic_feature || "");
}

function draftFor(finding?: GenomicFinding): GenomicFinding {
  return {
    gene: geneOf(finding ?? {}),
    variant: finding?.variant ?? finding?.variant_name ?? "",
    origin: finding?.origin ?? "",
    interpretation: finding?.interpretation ?? "",
    status: finding?.status ?? "present",
    ...(finding?.id != null ? { id: finding.id } : {}),
    ...(finding?.marker_key ? { marker_key: finding.marker_key } : {}),
  };
}

export function GenomicsFindingsDialog({
  label: fieldLabel,
  editing,
  onClose,
}: {
  label: string;
  editing: RowEditing;
  onClose: () => void;
}) {
  const adapter = editing.genomics;
  const [findings, setFindings] = useState<GenomicFinding[]>([]);
  const [markers, setMarkers] = useState<GenomicCatalogMarker[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [draft, setDraft] = useState<GenomicFinding | null>(null);
  const [deleting, setDeleting] = useState<GenomicFinding | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let current = true;
    if (!adapter) {
      setLoading(false);
      setLoadError(true);
      return;
    }
    setLoading(true);
    setLoadError(false);
    Promise.all([adapter.list(), adapter.catalog()])
      .then(([rows, catalog]) => {
        if (!current) return;
        setFindings(rows);
        setMarkers(catalog.markers ?? []);
      })
      .catch(() => {
        if (current) setLoadError(true);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [adapter, reloadKey]);

  const reload = () => setReloadKey((key) => key + 1);

  const refresh = async () => {
    if (!adapter) return;
    const rows = await adapter.list();
    setFindings(rows);
    editing.onGenomicsChanged?.(rows);
  };

  const save = async () => {
    if (!adapter || !draft) return;
    const gene = String(draft.gene ?? "").trim().toUpperCase();
    const variant = String(draft.variant ?? "").trim();
    if (!gene && !variant) {
      setError("Enter a gene or a finding.");
      return;
    }
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const payload: GenomicFinding = {
        ...draft,
        gene,
        genomic_feature: gene,
        feature_type: "Gene",
        variant,
      };
      if (draft.id != null) {
        await adapter.update(draft.id, payload);
        setStatus("Finding saved.");
      } else {
        await adapter.create(payload);
        setStatus("Finding added.");
      }
      setDraft(null);
      await refresh();
    } catch {
      setError("Could not save the finding. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!adapter || !deleting || deleting.id == null) return;
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await adapter.remove(deleting.id);
      setDeleting(null);
      setStatus("Finding deleted.");
      await refresh();
    } catch {
      setError("Could not delete the finding. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const genes = Array.from(
    new Set(
      markers
        .map((marker) => (marker.gene || marker.genomic_feature || "").toUpperCase())
        .filter(Boolean),
    ),
  ).sort();

  return (
    <Dialog label={`Genetic findings for ${fieldLabel}`} title="Genetic findings" onClose={onClose}>
      <p className="exact-subform__note">
        These are the findings behind &ldquo;{fieldLabel}&rdquo;. Each row is one gene result;
        save a row for every variant or interpretation you have.
      </p>

      {loading ? <p className="exact-subform__note">Loading findings…</p> : null}
      {loadError ? (
        <div className="exact-subform__note" role="alert">
          Could not load genetic findings.{" "}
          <button type="button" className="exact-btn-view" onClick={reload}>
            Retry
          </button>
        </div>
      ) : null}
      {error ? (
        <p className="exact-elig__error" role="alert">
          {error}
        </p>
      ) : null}
      {status ? <p className="exact-subform__note">{status}</p> : null}

      {!loading && !loadError ? (
        <>
          {findings.length === 0 && !draft ? (
            <p className="exact-subform__note">No genetic findings recorded.</p>
          ) : null}

          {findings.length ? (
            <ul className="exact-subform__list">
              {findings.map((finding) => (
                <li className="exact-subform__row" key={finding.id ?? `${geneOf(finding)}-${findingTitle(finding)}`}>
                  <span className="exact-subform__label">{geneOf(finding) || "—"}</span>
                  <span className="exact-subform__value">
                    {findingTitle(finding)} · {label(finding.origin)} · {label(finding.interpretation)} ·{" "}
                    {label(finding.status)}
                  </span>
                  <button
                    type="button"
                    className="exact-btn-view"
                    aria-label={`Edit ${geneOf(finding) || "finding"}`}
                    disabled={busy}
                    onClick={() => {
                      setDraft(draftFor(finding));
                      setDeleting(null);
                      setError("");
                      setStatus("");
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="exact-btn-view"
                    disabled={busy}
                    onClick={() => {
                      setDeleting(finding);
                      setDraft(null);
                      setError("");
                      setStatus("");
                    }}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          ) : null}

          {deleting ? (
            <div className="exact-subform__row" role="alert">
              <span className="exact-subform__label">Delete this finding?</span>
              <span className="exact-subform__value">
                {geneOf(deleting) || "—"} · {findingTitle(deleting)}
              </span>
              <button type="button" className="exact-btn-view" disabled={busy} onClick={remove}>
                Delete
              </button>
              <button type="button" className="exact-btn-view" disabled={busy} onClick={() => setDeleting(null)}>
                Cancel
              </button>
            </div>
          ) : null}

          {draft ? (
            <div className="exact-subform__row">
              <label className="exact-subform__label" htmlFor="exact-genomics-gene">
                Gene
              </label>
              <input
                id="exact-genomics-gene"
                list="exact-genomics-genes"
                value={draft.gene ?? ""}
                onChange={(event) => setDraft({ ...draft, gene: event.target.value })}
                placeholder="BRCA1"
              />
              <datalist id="exact-genomics-genes">
                {genes.map((gene) => (
                  <option key={gene} value={gene} />
                ))}
              </datalist>
            </div>
          ) : null}

          {draft ? (
            <div className="exact-subform__row">
              <label className="exact-subform__label" htmlFor="exact-genomics-variant">
                Variant / finding
              </label>
              <input
                id="exact-genomics-variant"
                value={draft.variant ?? ""}
                onChange={(event) => setDraft({ ...draft, variant: event.target.value })}
                placeholder="c.68_69delAG"
              />
            </div>
          ) : null}

          {draft ? (
            <div className="exact-subform__row">
              <label className="exact-subform__label" htmlFor="exact-genomics-origin">
                Origin
              </label>
              <select
                id="exact-genomics-origin"
                value={draft.origin ?? ""}
                onChange={(event) => setDraft({ ...draft, origin: event.target.value })}
              >
                {ORIGINS.map((origin) => (
                  <option key={origin || "unknown"} value={origin}>
                    {origin || "Unknown"}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          {draft ? (
            <div className="exact-subform__row">
              <label className="exact-subform__label" htmlFor="exact-genomics-interpretation">
                Interpretation
              </label>
              <select
                id="exact-genomics-interpretation"
                value={draft.interpretation ?? ""}
                onChange={(event) => setDraft({ ...draft, interpretation: event.target.value })}
              >
                {INTERPRETATIONS.map((interpretation) => (
                  <option key={interpretation || "unknown"} value={interpretation}>
                    {interpretation || "Unknown"}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          {draft ? (
            <div className="exact-subform__row">
              <label className="exact-subform__label" htmlFor="exact-genomics-status">
                Finding status
              </label>
              <select
                id="exact-genomics-status"
                value={draft.status ?? "present"}
                onChange={(event) => setDraft({ ...draft, status: event.target.value })}
              >
                {STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          <div className="exact-subform__row">
            {draft ? (
              <>
                <button type="button" className="exact-btn-view" disabled={busy} onClick={save}>
                  {busy ? "Saving…" : "Save finding"}
                </button>
                <button type="button" className="exact-btn-view" disabled={busy} onClick={() => setDraft(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                className="exact-btn-view"
                disabled={busy || Boolean(deleting)}
                onClick={() => {
                  setDraft(draftFor());
                  setError("");
                  setStatus("");
                }}
              >
                Add finding
              </button>
            )}
          </div>
        </>
      ) : null}
    </Dialog>
  );
}
