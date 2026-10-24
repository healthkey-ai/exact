# PatientRecord read adaptation in EXACT

This inventory compares CB `f85382e`'s `trials/matching/promop_surface.py`
with EXACT's `trials/services/patient_info/promop_adapter.py`. It records the
scope of #627, not a claim of complete CB/EXACT behavioral equivalence.
EXACT remains stateless: this path constructs a request-local DTO and never
writes to PROMOP or persists patient data.

## Canonical lab fallbacks (#627)

| Matcher field | PatientRecord source | Source unit / scale |
| --- | --- | --- |
| `white_blood_cell_count` | `wbc_count_thousand_per_ul` | Multiply by 1000, pair with `CELLS/UL` |
| `platelet_count` | `platelet_count_thousand_per_ul` | Multiply by 1000, pair with `CELLS/UL` |
| `hemoglobin_level` | `hemoglobin_g_dl` | `G/DL` |
| `lactate_dehydrogenase_level` | `ldh_u_l` | U/L, unchanged |
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
When a direct value is absent/null, its canonical fallback also supplies the
corresponding unit: a stale direct unit must not reinterpret the fallback.
Malformed/non-finite canonical readings remain unknown. This assumes the
named canonical source units; it does not repair upstream ingest-unit errors.

On this PROMOP path, a valid supplied eGFR (direct or canonical, including
zero) survives normalization even when the creatinine fallback enables a
fresh eGFR calculation. Without a valid source eGFR, existing derivation
still runs. The generic inline-payload normalizer is unchanged.

WBC is the exception: CB documents its populated alias as an unreliable,
unscaled copy. A non-null canonical WBC source therefore wins over that alias.
Old payloads without a canonical WBC reading retain their direct value/unit.
The adapter emits a coherent cells/µL pair; `get_value()` still converts to
EXACT's existing cells/L comparison basis. This port does **not** change the
WBC trial-threshold contract or introduce a cells/µL sane range on cells/L.

`preexisting_conditions` is a list of category **codes**, not category PKs.
The adapter seeds `_pre_existing_condition_codes_cache` on the request-local
patient after construction, as CB's surface does. It must not send these
codes through the inline builder's `pre_existing_condition_categories` PK
lookup. Empty lists remain unknown unless the separate asserted-none flag
settles that state. This does not change precedence for contradictory
`no_pre_existing_conditions` inputs (CB #5501).

## Remaining CB surface inventory

These are explicit scope boundaries, not allowances to conceal new drift:

| CB surface entry/group | EXACT status / owner |
| --- | --- |
| `stem_cell_transplant_date <- sct_date` | Existing clinical adapter from #624; derived days are matcher-owned |
| PT/aPTT ULN and swallowing | Existing same-name source fields from #624 |
| ANC and its paired unit; ALC | Existing fallbacks still have co-populated-alias scaling gaps: #628 |
| `disease` case/whitespace adapter | CB explicitly normalizes supported titles; not changed by this lab slice |
| `gender` (`U -> UN`) | EXACT has its own gender normalization/unknown handling; not asserted equivalent here |
| `patient_age` unknown-DOB sentinel guard | CB checks Person provenance; EXACT's flat row adapter does not establish the same provenance. Remains a known audit boundary, not a guessed age correction |
| `cytogenic_markers` spelling and vocabulary | #590/#461; not duplicated in #627 |
| `lactate_dehydrogenase_level_uln <- ldh_upper_limit_normal` | #631; DTO/input and MIPI derivation change together |
| Supportive therapy text -> structured rows | Intentionally unresolved; CB treats it as type-incompatible, EXACT has existing text clearing; #231/#384 |
| RBC | Not a canonical fallback in CB `_FIELD_MAPS`; existing host contract differs, outside #627 |
| Direct bilirubin | No corresponding PatientRecord canonical source in the audited contract; do not substitute total bilirubin |

Other CB `_FIELD_MAPS` entries are the paired `*_units` fields listed above.
CB's derived routes (MIPI, adequacy, progression), therapy-unknown filtering,
geography and schema metadata are outside the scalar fallback table. Related
behavioral work: #626, #629, #630, #621 W3; cross-host regression gate:
`cancerbot-org/cancerbot#5510`. No OMOP flag, CB dependency pin or deployment
authorization changes as part of this contract.
