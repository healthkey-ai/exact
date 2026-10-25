# PatientRecord read adaptation in EXACT

This inventory compares CB `f85382e`'s `trials/matching/promop_surface.py`
with EXACT's `trials/services/patient_info/promop_adapter.py`. It records the
scope of #627, not a claim of complete CB/EXACT behavioral equivalence.
EXACT remains stateless: this path constructs a request-local DTO and never
writes to PROMOP or persists patient data.

## Canonical lab fallbacks (#627, #631)

| Matcher field | PatientRecord source | Source unit / scale |
| --- | --- | --- |
| `white_blood_cell_count` | `wbc_count_thousand_per_ul` | Multiply by 1000, pair with `CELLS/UL` |
| `platelet_count` | `platelet_count_thousand_per_ul` | Multiply by 1000, pair with `CELLS/UL` |
| `hemoglobin_level` | `hemoglobin_g_dl` | `G/DL` |
| `lactate_dehydrogenase_level` | `ldh_u_l` | U/L, unchanged |
| `lactate_dehydrogenase_level_uln` | `ldh_upper_limit_normal` | U/L, unchanged; MIPI uses it, absent/non-positive falls back to 250 |
| `liver_enzyme_levels_alt` | `alt_u_l` | U/L, unchanged |
| `liver_enzyme_levels_ast` | `ast_u_l` | U/L, unchanged |
| `liver_enzyme_levels_alp` | `alkaline_phosphatase_u_l` | U/L, unchanged |
| `creatinine_clearance_rate` | `creatinine_clearance_ml_min` | mL/min, unchanged |
| `estimated_glomerular_filtration_rate` | `egfr_ml_min_173m2` | mL/min/1.73m², unchanged |
| `serum_calcium_level` | `serum_calcium_mg_dl` | `MG/DL` |
| `serum_creatinine_level` | `serum_creatinine_mg_dl` | `MG/DL` |
| `serum_bilirubin_level_total` | `bilirubin_total_mg_dl` | `MG/DL` |
| `pd_l1_tumor_cels` | `pd_l1_tumor_cells` | Same percentage; matcher name retains its historical typo |

For these fallbacks, reliable direct values take precedence, including zero.
When a direct value is absent/null/blank text, its canonical fallback also supplies the
corresponding unit: a stale direct unit must not reinterpret the fallback.
Malformed/non-finite canonical readings remain unknown. This assumes the
named canonical source units; it does not repair upstream ingest-unit errors.

For inputs carrying canonical renal-source columns, a valid supplied eGFR
(direct or canonical, including zero) survives normalization even when the
creatinine fallback enables a fresh eGFR calculation. Both the server-side
PROMOP builder and normalize-promop-row -> inline federation use this DTO
boundary. Without a valid source eGFR, existing derivation still runs.
Ordinary legacy inline payloads without those canonical renal-source columns
retain their existing normalization behavior. No private/trusted marker is
accepted from a client; source values remain ordinary untrusted clinical input.

The preserved value is the **published** eGFR, which the direct
`estimatedGlomerularFiltrationRateMin/Max` criteria read. The aggregate
`renal_adequacy_status` is a different question and keeps its existing
conservative rule: it screens on the worse of the supplied and the derived
eGFR. A supplied eGFR of 90 beside a creatinine implying ~20 therefore publishes
90 and satisfies an explicit eGFR threshold, while the adequacy flag stays
negative. That asymmetry predates this port on the CB side and is intentional;
it is not a second source of truth. Negative or otherwise out-of-band values
are passed through as readings here, as they already are on the direct path;
range guards are tracked in #632.

WBC is the exception: CB documents its populated alias as an unreliable,
unscaled copy. A non-blank canonical WBC source therefore wins over that alias,
including one that cannot be parsed — the alias may be the 1000x-low copy, so
the honest answer is unknown. A blank/whitespace canonical WBC is treated as
"no canonical reading" and leaves an existing direct value/unit intact.
Old payloads without a canonical WBC reading retain their direct value/unit.

ANC and ALC follow the same rule for the same reason (#628): CB reads ANC from
`anc_thousand_per_ul` (the direct column is a co-populated copy of the same
number, 1000x low) and ALC from `alc_thousand_per_ul` (the direct column was
filled without the ×1000). Both scale by 1000 into cells/µL; ANC also pairs the
`CELLS/UL` unit, which `get_value()` then converts to EXACT's configured basis.
ALC has no unit field on either side.
In this path a reading must also survive the matcher's own unit conversion.
EXACT compares WBC on its documented cells/L basis, so a finite cells/µL
source value can still overflow to infinity when converted. A conversion that
leaves the finite range is reported as unknown rather than published or
compared; ordinary values are unaffected. The cells/L contract itself is not
changed.

`preexisting_conditions` is a list of category **codes**, not category PKs.
The DTO boundary seeds `_pre_existing_condition_codes_cache` on the request-local
patient, as CB's surface does, for both the PROMOP builder and federation inline
path. It must not send these
codes through the inline builder's `pre_existing_condition_categories` PK
lookup. Empty lists remain unknown unless the separate asserted-none flag
settles that state. This does not change precedence for contradictory
`no_pre_existing_conditions` inputs (CB #5501).
If a caller also supplies the legacy category-PK input, that explicit native
input retains its existing precedence; an empty source list must not hide
its categories. Without PK input, non-string/blank elements are ignored
without discarding known source codes. Graph serialization uses the same
category-code reader as matching so those codes are not lost in responses.

## Remaining CB surface inventory

These are explicit scope boundaries, not allowances to conceal new drift:

| CB surface entry/group | EXACT status / owner |
| --- | --- |
| `stem_cell_transplant_date <- sct_date` | Existing clinical adapter from #624; derived days are matcher-owned |
| PT/aPTT ULN and swallowing | Existing same-name source fields from #624 |
| ANC and its paired unit; ALC | Canonical thousands/µL source wins over the unscaled/unreliable alias for ANC and ALC too (#628) |
| `disease` case/whitespace adapter | CB explicitly normalizes supported titles; not changed by this lab slice |
| `gender` (`U -> UN`) | EXACT has its own gender normalization/unknown handling; not asserted equivalent here |
| `patient_age` unknown-DOB sentinel guard | CB checks Person provenance; EXACT's flat row adapter does not establish the same provenance. Remains a known audit boundary, not a guessed age correction |
| `cytogenic_markers` spelling and vocabulary | #590/#461; not duplicated in #627 |
| Supportive therapy text -> structured rows | Intentionally unresolved; CB treats it as type-incompatible, EXACT has existing text clearing; #231/#384 |
| RBC | Not a canonical fallback in CB `_FIELD_MAPS`; existing host contract differs, outside #627 |
| Direct bilirubin | No corresponding PatientRecord canonical source in the audited contract; do not substitute total bilirubin |

Other CB `_FIELD_MAPS` entries are the paired `*_units` fields listed above.
CB's derived routes (MIPI, adequacy, progression), therapy-unknown filtering,
geography and schema metadata are outside the scalar fallback table. Related
behavioral work: #626, #629, #630, #621 W3; cross-host regression gate:
`cancerbot-org/cancerbot#5510`. No OMOP flag, CB dependency pin or deployment
authorization changes as part of this contract.
