"""PROMOP `PatientRecord` presented through the matcher's `PatientInfo` surface (#5523).

The trial matcher / queryset duck-types a `PatientInfo`-shaped object: it reads the clinical
fields by name, reads `patient.geo_point` (distance is a HARD eligibility filter), and classifies
field types via `patient.__class__._meta.get_field(attr)` / marks derived fields read-only via
`patient.__class__.DERIVED_FIELDS_IN_SAVE`.

This is the package-owned half of that surface, extracted from CB's
`trials/matching/promop_surface.py` (CB #4606) so that the PROMOP-to-matcher field mapping
is owned by EXACT rather than by the host (#5523). The host supplies the two things that are
genuinely its own:

- **Schema** (`_meta`, `DERIVED_FIELDS_IN_SAVE`): the matcher's field vocabulary, a class
  attribute of the *host's* `PatientInfo`. Subclass `PatientRecordSurface` and set them; they
  are read as `patient.__class__.<attr>`, so they cannot be constructor arguments.
- **Geography**: `geo_point` is passed in. The record has no geometry column, and resolving
  country/postal to coordinates is a host catalog lookup (`derive_geo_point` in CB), not a
  patient-field mapping.

Everything else — field aliases, unit rescales, cytogenetic vocabulary translation, the
derived-property routing, the therapy-unknown filter and the pre-existing-condition cache
seeding — lives here and is covered by this package's tests.

`attributes_factory` defaults to this package's own `PatientInfoAttributes`, the copy the
matcher itself uses. A host may inject its own engine during migration; the parity gate must
prove the two agree before the injection is removed.

It never persists anything and lives for one request.
"""
from django.core.exceptions import ObjectDoesNotExist

#: PROMOP's unknown date-of-birth sentinel. `patient_age` derived from it is not
#: an age (#4887). Kept as a module constant rather than injected: it is a
#: PROMOP data convention, not host configuration, and a host that disagreed
#: about it would be reading a different patient anyway.
UNKNOWN_YEAR_OF_BIRTH = 1900


def _scaled(value, factor):
    """Multiply a numeric value by a unit-scale factor, preserving None (= unknown)."""
    return None if value is None else value * factor


def _prefer_direct(record, field, otherwise):
    """The direct CB-parity value if PROMOP populated it (post pin-bump #4800 dev derives several
    directly), else the fallback conversion. Keeps the maps non-throwaway across the bump."""
    direct = getattr(record, field, None)
    return direct if direct is not None else otherwise


#: PROMOP's canonical cytogenetic markers -> CB's `CytogenicMarker.code`. The two
#: vocabularies only overlap on `hyperdiploidy`; CB's matcher and its
#: `cytogenic_markers_required/excluded` trial codes speak CB's codes, so a raw
#: PROMOP string would never match. `del13q` has no CB code; CB's
#: `complexKaryotype`/`complexKaryotypeExcludingT1114` have no PROMOP value yet, so
#: those criteria stay unknown instead of being guessed (#5483).
CYTOGENETIC_MARKERS_TO_CB = {
    'del17p': 'del17p13',
    't(4;14)': 't414',
    't(11;14)': 't1114',
    't(14;16)': 't1416',
    '1q_gain': '1q21Amplification',
    '1q_amp': '1q21Amplification',
    'hyperdiploidy': 'hyperdiploidy',
    'MYC rearrangement': 'mycRearrangements',
}


def _cytogenetic_value(record):
    """PROMOP's marker summary under either column name.

    The pin CB runs today still calls it `cytogenic_markers`; the newer PROMOP line
    (migration 0222) renamed it to `cytogenetic_markers`. Read the new name first so a
    re-pin needs no change here, and fall back for the current pin.
    """
    value = getattr(record, 'cytogenetic_markers', None)
    return value if value is not None else getattr(record, 'cytogenic_markers', None)


def _cytogenetic_markers(value):
    """PROMOP's marker string in CB's vocabulary, comma-separated as CB expects.

    PROMOP stores `', '.join(canonical)` and a canonical value can itself carry a
    comma (`t(4;14)`), so split on the separator rather than on every comma. A value
    with no CB counterpart (an unrecognised import, or `del13q`) is dropped: CB has
    no code for it, and the rest of the set stays exact. A wholly unrecognised value
    returns '' so the criterion reads unknown rather than "no markers".
    """
    if not value:
        return ''
    codes = []
    for raw in str(value).split(', '):
        code = CYTOGENETIC_MARKERS_TO_CB.get(raw.strip())
        if code and code not in codes:
            codes.append(code)
    return ','.join(codes)



def _age_unless_sentinel(record):
    """`patient_age`, or None when it was derived from PROMOP's unknown-DOB sentinel (#4887).

    Resolves the Person to read `year_of_birth` directly. If the Person cannot be reached at all the
    age's provenance is unknown, so it is reported as unknown rather than trusted — the sentinel is
    precisely the case that would otherwise leak through.
    """
    try:
        year_of_birth = record.person.year_of_birth
    except (AttributeError, ObjectDoesNotExist):
        return None
    return None if year_of_birth == UNKNOWN_YEAR_OF_BIRTH else record.patient_age


def _derive_progression(surface):
    """Replicate CB's `PatientInfo.save` progression rule from meets_crab / meets_slim (both PROMOP-
    derived). 'active' if either CRAB/SLiM is met, 'smoldering' if both are explicitly not met, else
    None (unknown) — matching CB's `is True` / `is False` guards, so an unknown input stays unknown."""
    meets_crab, meets_slim = surface.meets_crab, surface.meets_slim
    if meets_crab is True or meets_slim is True:
        return 'active'
    if meets_crab is False and meets_slim is False:
        return 'smoldering'
    return None


#: §6 conversion table (#4803): CB-parity matcher field ← its PROMOP source, for fields that PROMOP
#: derives under a differently-named or differently-scaled column (semantic-gap §3/§4.1). Each entry
#: is a full override function of the PatientRecord that already embeds **prefer-direct-else-convert**.
#:
#: **Unit coupling (important):** the matcher's `get_value()` converts the CBC counts using their
#: `*_units` field, so a value in cells/µL MUST be paired with a `CELLS/UL` unit — PROMOP defaults the
#: unit to `CELLS/L`, which would make the matcher rescale 7500 → 0.0075. So for a *mapped* count the
#: `*_units` field is forced to `CELLS/UL`; when the direct CB-parity value is used instead, the unit
#: tracks PROMOP's (value+unit stay self-consistent).
#:
#: Deliberately NOT here yet (later #4803 slices): `red_blood_cell_count` (a genuine meaning-differs —
#: CB unit CELLS/L + DecimalField overflow; delegated → None/unknown for now, no unit hazard since the
#: matcher does not convert a None value); `supportive_therapies` (Text→list). (Group N is a no-op —
#: the matcher coerces None→False for bool_restriction. The five Group-D re-derivations are handled
#: separately via `_DERIVED_VIA_ATTRIBUTES` + `progression` below, not through _FIELD_MAPS.)

#: PROMOP writes the *label* for refractory status; EXACT compares codes, and its
#: matcher treats **any** other non-empty value as refractory
#: (`matcher.py:756-775`). So an unrecognised label is not a harmless
#: pass-through: `Unknown` and `Not Refractory` both currently read as
#: "refractory". Unknown labels become None (unknown) rather than a clinical
#: assertion (#5552).
REFRACTORY_STATUS_TO_CB = {
    'not refractory': 'notRefractory',
    'notrefractory': 'notRefractory',
    'primary refractory': 'primaryRefractory',
    'primaryrefractory': 'primaryRefractory',
    'secondary refractory': 'secondaryRefractory',
    'secondaryrefractory': 'secondaryRefractory',
    'multi-refractory': 'multiRefractory',
    'multi refractory': 'multiRefractory',
    'multirefractory': 'multiRefractory',
    'refractory': 'refractory',
    'unknown': None,
    '': None,
}


def refractory_status_code(value):
    """PROMOP's refractory label → EXACT's code, or None for "unknown".

    Already-canonical codes pass through. Anything unrecognised is treated as
    unknown on purpose: the matcher's fallback turns an arbitrary string into a
    positive refractory assertion.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    mapped = REFRACTORY_STATUS_TO_CB.get(text.casefold())
    if mapped is not None or text.casefold() in ('unknown', ''):
        return mapped
    if text in REFRACTORY_STATUS_TO_CB.values():
        return text
    return None


_FIELD_MAPS = {
    # SCT is already an OMOP-derived date on the pinned projection. Keep it
    # PROMOP-owned; the host attribute service derives days and handles future
    # dates as unknown. No stored verdict or CB date fallback crosses this seam.
    'stem_cell_transplant_date': lambda r: r.sct_date,
    # PROMOP stores the label (`Not Refractory`, `Unknown`); EXACT expects the
    # code and otherwise treats any non-empty value as refractory (#5552).
    'treatment_refractory_status': lambda r: refractory_status_code(getattr(r, 'treatment_refractory_status', None)),
    # Group U — unit ×scale (10³/µL → cells/µL), value + its canonical unit as a coherent pair.
    #
    # WBC and ANC convert ALWAYS from the canonical `*_thousand_per_ul` column (×1000 → cells/µL) and
    # NEVER _prefer_direct, because after the promop `cb`←`dev` back-merge (#2omop-federated-UI) the
    # derivation populates their direct legacy columns with the 10³/µL value UNSCALED, not cells/µL:
    #   • `white_blood_cell_count`      = canonical WBC (10³/µL), with `_units` = canonical_wbc_unit()
    #     (a 10³/µL unit, NOT 'CELLS/UL') — patient_record_service `_LOINC_LAB_FIELDS` 6690-2 block.
    #     WBC's canonical is unit-normalized by `wbc_to_canonical`, so it is reliably 10³/µL.
    #   • `absolute_neutrophile_count`  = `anc_thousand_per_ul` copied verbatim via `_LAB_FIELD_ALIASES`
    #     (#471); the direct column is nothing but a co-populated copy of the canonical (no independent
    #     value), so _prefer_direct would read the same 10³/µL number 1000× low.
    # Preferring those direct columns (as the pre-back-merge maps did) reads 1000× low — the same failure
    # ALC (#4825) documents below. The canonical column is derived from the same fact and is always
    # co-populated, so always-convert loses no data; the paired `*_units` is therefore UNCONDITIONALLY
    # 'CELLS/UL' (matches the ×1000 value; the matcher's get_value convertor then reads it as identity).
    # CAVEAT (ANC): unlike WBC, `anc_thousand_per_ul` is a raw `value_as_number` copy with NO ingest unit
    # normalization, so an externally-imported ANC fact in cells/µL would be mis-scaled ×1000. The unit is
    # discarded upstream (the canonical column carries no companion unit), so the seam cannot recover it —
    # the real fix is upstream ingest normalization, tracked as promop#640 (same class as ALC #4825). The
    # CB write path (which this round-trip covers) always stores 10³/µL, so this is correct for the
    # integration's own writes; prefer-direct is NOT an alternative (it reads the co-populated 10³/µL alias
    # 1000× low and breaks the CB round-trip).
    'white_blood_cell_count':
        lambda r: _scaled(r.wbc_count_thousand_per_ul, 1000),
    'white_blood_cell_count_units':
        lambda r: 'CELLS/UL',
    'absolute_neutrophile_count':
        lambda r: _scaled(r.anc_thousand_per_ul, 1000),
    'absolute_neutrophile_count_units':
        lambda r: 'CELLS/UL',
    # platelet_count — KEEPS _prefer_direct (unlike WBC/ANC). Its direct `platelet_count` column is NOT
    # the 1000×-low alias copy: `_LEGACY_LAB_CONCEPT_FIELDS` ('platelet count' → 'platelet_count',
    # patient_record_service ~L477) fills it from the raw source `value_as_number` WITH its own paired
    # unit, and it is NOT co-populated with `platelet_count_thousand_per_ul` (a SNOMED-coded platelet
    # count populates only the direct column). Always-scaling from the canonical would silently drop
    # that value to None. So prefer the direct value (correct value+unit) and fall back to the ×1000
    # canonical (#4764: CB's canonical is now cells/µL) when only the LOINC/source-value path populated it.
    'platelet_count':
        lambda r: _prefer_direct(r, 'platelet_count', _scaled(r.platelet_count_thousand_per_ul, 1000)),
    'platelet_count_units':
        lambda r: r.platelet_count_units if r.platelet_count is not None else 'CELLS/UL',
    # absolute_lymphocyte_count (MCL/CLL lymphocytosis, cells/µL) — ×1000 from the 10³/µL source, no
    # *_units field. Deliberately NOT _prefer_direct here (#4825): the PROMOP `absolute_lymphocyte_count`
    # column is unreliable — `_get_cll_data` (patient_record_service, LOINC 731-0) copies the Measurement's
    # 10³/µL value straight into this cells/µL parity field WITHOUT the ×1000, and it runs unconditionally
    # (not just for CLL), so for ANY patient with an ALC fact the direct column reads 1000× low.
    # `alc_thousand_per_ul` is derived from the SAME 731-0 fact by the general CBC map and is correctly
    # 10³/µL. Whenever the direct column is populated `alc_thousand_per_ul` is too (same fact), so no data
    # is lost by ignoring the direct column; and `alc_thousand_per_ul` can be populated when the direct
    # column is NOT (a source-value-only ALC fact), where the direct column would be untrustworthy anyway —
    # so always converting from `alc_thousand_per_ul` is strictly the safer source.
    # (Root cause is PROMOP-side; tracked as a promop derivation issue. This defends the matcher meanwhile.)
    'absolute_lymphocyte_count':
        lambda r: _scaled(r.alc_thousand_per_ul, 1000),
    # Demographics (#4887). PROMOP's derivation speaks its own dialect for both of these, so the
    # surface converts them back into the vocabulary the matcher's configs are written against.
    #
    # gender: the derivation emits 'M' / 'F' / 'U' (patient_record_service), but CB's GenderChoices are
    # 'M' / 'F' / 'UN' / ''. Only 'U' differs, and left alone it reaches the matcher as a code that
    # exists in no CB option list.
    'gender': lambda r: 'UN' if r.gender == 'U' else r.gender,
    # patient_age: `ensure_person_linked` seeds a placeholder Person with year_of_birth=1900 (PROMOP's
    # own unknown-DOB convention), and the derivation subtracts it blind — so a CB-originated patient
    # with no age surfaced as `today.year - 1900`, i.e. 126 in 2026. That is not "unknown", it is a
    # confident wrong number driving every age criterion.
    #
    # Test the SOURCE, not the arithmetic. `PatientRecord.patient_age` is materialised by
    # `refresh_patient_record` and is not in `_OMOP_DERIVED_FIELDS`, so it is only recomputed when some
    # signalled write happens — a row derived in 2025 still stores 125. Comparing against
    # `today.year - 1900` would therefore stop recognising the sentinel every New Year, and would also
    # suppress a stale 126 that legitimately came from year_of_birth 1899. Reading `year_of_birth` is
    # exact and year-independent instead. It does discard the age of someone genuinely born in 1900 —
    # unavoidable while the sentinel IS a real year — but no other outlier.
    #
    # Fails SAFE. `RelatedObjectDoesNotExist` subclasses `AttributeError`, so a plain `getattr(...,
    # None)` default would swallow exactly the case where the guard cannot do its job and hand the
    # matcher the raw 126 back. Unknown provenance ⇒ unknown age.
    'patient_age': _age_unless_sentinel,
    # Group R — rename, same unit (no *_units conversion applies to these).
    # Read only the upstream SCT date projection. Missing/future dates remain
    # unknown in PatientInfoAttributes; never substitute the native CB date.
    # This is a read adapter, not evidence of host source-write support.
    'stem_cell_transplant_date': lambda r: r.sct_date,
    'lactate_dehydrogenase_level': lambda r: _prefer_direct(r, 'lactate_dehydrogenase_level', r.ldh_u_l),
    'liver_enzyme_levels_alt': lambda r: _prefer_direct(r, 'liver_enzyme_levels_alt', r.alt_u_l),
    'liver_enzyme_levels_ast': lambda r: _prefer_direct(r, 'liver_enzyme_levels_ast', r.ast_u_l),
    'liver_enzyme_levels_alp': lambda r: _prefer_direct(r, 'liver_enzyme_levels_alp', r.alkaline_phosphatase_u_l),
    'creatinine_clearance_rate': lambda r: _prefer_direct(r, 'creatinine_clearance_rate', r.creatinine_clearance_ml_min),
    'pd_l1_tumor_cels': lambda r: r.pd_l1_tumor_cells,  # CB field name is a typo of the PROMOP column
    'cytogenic_markers': lambda r: r.cytogenetic_markers,  # PROMOP corrected the legacy spelling
    # Organ-function labs (#4827): the matcher reads the CB-parity name, but the RELIABLE PROMOP source
    # is a differently-named LOINC-coded column. `_get_laboratory_data` also fills the direct CB-parity
    # column via concept-NAME substring matching (with the correct paired unit) — but eGFR has NO legacy
    # name mapping (its direct column is ALWAYS NULL), and the name match is fragile for the others. So
    # prefer the direct value (right unit, paired) when present, else fall back to the canonical
    # LOINC-coded column (SAME unit). Unlike ALC (#4825), the direct columns here carry the correct unit,
    # so _prefer_direct is safe. Pair each *_units to the field's default_unit so get_value's convertor
    # (which reads the *_units attr) is identity when the canonical fallback is used.
    # KNOWN upstream P3 (pre-existing, unchanged by this map): `_get_laboratory_data` fills each direct
    # column by loose concept-NAME substring — 'hemoglobin' also matches "Hemoglobin A1c", 'calcium'
    # matches ionized, 'creatinine' matches "creatinine clearance", 'bilirubin' matches "direct
    # bilirubin" — so a direct column can hold a clinically wrong analyte/scale that then wins here. Root
    # fix is a tighter PROMOP name match (promop#545); this map only ADDS the canonical LOINC fallback.
    # Also (promop#545, unit dimension): the canonical *_g_dl/*_mg_dl columns are `value_as_number` copied
    # WITHOUT converting `unit_source_value`, so they assume the source lab is already in the named unit;
    # a lab ingested in SI units (e.g. hemoglobin g/L) would read 10× off. PROMOP must normalize on ingest;
    # the surface can't (the canonical column doesn't carry its unit). Common case (US/LOINC default unit)
    # is correct, and this is strictly better than the pre-fix read-gap (unknown), so it does not block.
    'estimated_glomerular_filtration_rate':
        lambda r: _prefer_direct(r, 'estimated_glomerular_filtration_rate', r.egfr_ml_min_173m2),  # no *_units
    'hemoglobin_level':
        lambda r: _prefer_direct(r, 'hemoglobin_level', r.hemoglobin_g_dl),
    'hemoglobin_level_units':
        lambda r: r.hemoglobin_level_units if r.hemoglobin_level is not None else 'g/dL',
    'serum_calcium_level':
        lambda r: _prefer_direct(r, 'serum_calcium_level', r.serum_calcium_mg_dl),
    'serum_calcium_level_units':
        lambda r: r.serum_calcium_level_units if r.serum_calcium_level is not None else 'mg/dL',
    # serum_creatinine / total-bilirubin — same Group-R pattern (rename, no scale; SimpleCToSiConvertor
    # cf 88.42/17.1 is identity when from==to, so the paired 'mg/dL' unit keeps the canonical fallback
    # un-rescaled). This enables the ABSOLUTE-threshold match (trial *_abs_min/max, read via the mapping
    # key here). The ULN-RATIO paths (get_uln_value → Scr/BiliT UlnCalculator) key on race/ethnicity
    # vocab and stay a KNOWN-INTERIM miss→None (same as hepatic_adequacy, promop#526) — unaffected here.
    'serum_creatinine_level':
        lambda r: _prefer_direct(r, 'serum_creatinine_level', r.serum_creatinine_mg_dl),
    'serum_creatinine_level_units':
        lambda r: r.serum_creatinine_level_units if r.serum_creatinine_level is not None else 'mg/dL',
    'serum_bilirubin_level_total':
        lambda r: _prefer_direct(r, 'serum_bilirubin_level_total', r.bilirubin_total_mg_dl),
    'serum_bilirubin_level_total_units':
        lambda r: r.serum_bilirubin_level_total_units if r.serum_bilirubin_level_total is not None else 'mg/dL',
    # cytogenic_markers — PROMOP renamed the column (`cytogenic` -> `cytogenetic`) and
    # owns its own canonical vocabulary, so translate to CB's codes on the way in
    # (#5483, W6). Without this the field reads None for every PROMOP patient and the
    # MCL high-risk criteria derived from it stay unknown.
    'cytogenic_markers':
        lambda r: _cytogenetic_markers(_cytogenetic_value(r)),
    # lactate_dehydrogenase_level_uln — MIPI reads the patient/lab-specific LDH upper
    # limit of normal and falls back to a fixed 250 when it is unset. PROMOP stores it
    # under its own name on the line CB is moving to; on the current pin the column is
    # absent and this reads None (the documented fallback), so the map takes effect with
    # the re-pin (#5483). `getattr`, not attribute access: the pin transition is exactly
    # when the column is missing.
    'lactate_dehydrogenase_level_uln':
        lambda r: getattr(r, 'ldh_upper_limit_normal', None),
    # NOTE: serum_bilirubin_level_direct is intentionally NOT mapped — PROMOP has no direct-bilirubin
    # source column, so it stays None (unknown); a separate follow-up if/when a source is derived.
}


#: §6 Group D (#4803): CB-derived matcher fields that PROMOP does NOT derive (no column, or a NULL
#: column) even on `dev`. Rather than re-implement CB's derivation formulas (MIPI scoring, the ULN-based
#: adequacy checks) and risk divergence, the surface re-runs **CB's own** `PatientInfoAttributes` over
#: itself — the derivations read only input fields (age/ECOG/WBC/LDH/ANC/platelet/bilirubin/AST/ALT/…),
#: which the surface supplies (with the §6 Group U/R maps applied), and none read the derived field
#: itself, so there is no recursion. `progression` is not on `PatientInfoAttributes` (CB computes it in
#: `PatientInfo.save`) and is handled explicitly below.
#:
#: **KNOWN INTERIM (hepatic_adequacy_status):** its ULN calculators key on `patient_info.ethnicity`
#: using CB's *race* vocabulary (`caucasian_or_european`/`asian`/…). CB conflates race into its single
#: `ethnicity` field; PROMOP splits ethnicity (OMOP "Hispanic or Latino"/"Not…") from `race` (OMOP race
#: labels), neither of which matches CB's codes. So the ULN lookup misses and hepatic currently
#: **fails closed to `False`** (conservative — never falsely includes). Making it return `True` needs a
#: race-vocabulary conversion (`ethnicity ← mapped(record.race)`), a genuine meaning-differs deferred
#: with `red_blood_cell_count` / `supportive_therapies` (see the _FIELD_MAPS deferred note + #4803).
#: The other four Group-D fields (MIPI, haematological, progression) do NOT depend on ethnicity and are
#: fully live.
_DERIVED_VIA_ATTRIBUTES = frozenset({
    'mipi_risk', 'mipi_c_risk', 'hepatic_adequacy_status', 'haematological_adequacy_status',
})

#: PROMOP's marker for an asserted line whose composition it cannot name (#1924).
#: The line is real — `therapy_lines_count` counts it — but its regimen is unknown, so the
#: matcher must see no therapy evidence rather than a therapy called "Unknown" (#5485).
#: Read literally, the sentinel compares against `therapies_required`, misses, and excludes
#: every trial that requires a regimen the patient may well have had.
_UNKNOWN_THERAPY_MARKERS = frozenset({'unknown'})

#: Columns that hold a line's name as a plain string (the `later_therapies` list holds them
#: as `therapy` keys instead — see `without_unknown_lines`).
_THERAPY_LINE_NAME_FIELDS = frozenset({
    'first_line_therapy', 'second_line_therapy', 'later_therapy',
})


def _is_unknown_therapy(value):
    return isinstance(value, str) and value.strip().casefold() in _UNKNOWN_THERAPY_MARKERS


def without_unknown_lines(rows):
    """`later_therapies` rows with the unknown-composition marker removed.

    `therapy_codes()` ignores a row whose `therapy` is None, so the line keeps its date and
    line number in the payload but contributes no regimen to the match.
    """
    if not isinstance(rows, list):
        return rows
    return [
        {**row, 'therapy': None}
        if isinstance(row, dict) and _is_unknown_therapy(row.get('therapy'))
        else row
        for row in rows
    ]


class PatientRecordSurface:
    #: Fields this surface NAMES but derives itself. A null derivation then means
    #: "unknown", not "the caller never supplied it": PROMOP has no adequacy
    #: column, so a patient without the labs is undecided, not "not adequate"
    #: (#5483, via `explicitly_unknown`). Only the adequacy fields; widening this
    #: to tnbc/metastatic is the open half of #502.
    _provided_fields = frozenset({
        'hepatic_adequacy_status', 'haematological_adequacy_status',
    })

    #: Class-level schema the matcher reads as `patient.__class__.<attr>`. It is the matcher's
    #: field vocabulary (every classified attribute, including the ones `PatientRecord` does not
    #: carry), so the host owns it: subclass this surface and set `_meta` / `DERIVED_FIELDS_IN_SAVE`
    #: from its `PatientInfo`. They cannot be constructor arguments because the matcher reads them
    #: off the class. `None` / `()` fail loudly in `hasattr`-style probes rather than passing a
    #: surface that silently loses its schema.
    _meta = None
    DERIVED_FIELDS_IN_SAVE = ()

    #: Fields whose PatientRecord storage type is incompatible with what the matcher expects from
    #: CB's schema, so the raw value would crash the matcher — return None ("unknown") until a real
    #: conversion is defined. `supportive_therapies` is CB `JSONField` (a list of therapy dicts the
    #: matcher iterates and `.get()`s) but PatientRecord `TextField` (a string) — handing the matcher
    #: the string makes it iterate characters and call `str.get`. This set is the seam where §6
    #: conversion-table rows (Text→structured, unit rescales) get implemented as the feed matures.
    _TYPE_INCOMPATIBLE_FIELDS = frozenset({'supportive_therapies'})

    def __init__(self, patient_record, geo_point=None, attributes_factory=None):
        object.__setattr__(self, '_record', patient_record)
        # Derived once per request by the host (the matcher reads `geo_point` for the hard
        # distance filter). Passing it in keeps the geocoding catalog — country/state lookups —
        # out of the package, and keeps this surface free of host model imports.
        object.__setattr__(self, '_geo_point', geo_point)
        # Defaults to this package's own engine: the copy the matcher already uses. A host may
        # inject its own during migration; the parity gate removes the injection.
        object.__setattr__(self, '_attributes_factory', attributes_factory)
        # `pre_existing_condition_categories` is NOT a plain value on CB PatientInfo — it is a related
        # manager the matcher reads via `.values_list('category__code', ...)` (patient_info_attributes
        # `get_value`). PatientRecord has no such manager, so a bare `__getattr__ -> None` would make
        # the matcher call `None.values_list(...)` and crash on every match. PROMOP already carries the
        # equivalent as the flat `preexisting_conditions` code list, so pre-seed the matcher's
        # request-scoped memo (`_pre_existing_condition_codes_cache`) with it — `get_value` then uses
        # the memo and never dereferences the absent manager. Empty on the current pin (the derivation
        # is a pending promop-ask, #525) → the matcher reads it as blank/unknown; real codes flow once
        # PROMOP derives `preexisting_conditions`. (This is the Option-B read path, #4747/#4803.)
        object.__setattr__(self, '_pre_existing_condition_codes_cache',
                           list(getattr(patient_record, 'preexisting_conditions', None) or []))

    @property
    def geo_point(self):
        return object.__getattribute__(self, '_geo_point')

    @property
    def no_pre_existing_conditions(self):
        """Recorded categories take precedence over a contradictory legacy flag (#5501).

        Both matcher implementations and ProfileReadProjection read this answer.
        Mask the stale "none" assertion, not the categories, without changing the
        persisted record. Empty categories still require an explicit True: absence
        of evidence never becomes a negative answer here.
        """
        if object.__getattribute__(self, '_pre_existing_condition_codes_cache'):
            return None
        return getattr(object.__getattribute__(self, '_record'),
                       'no_pre_existing_conditions', None)

    def _provenance_value(self, name):
        """The record's own value, for provenance checks — never re-derives.

        ``explicitly_unknown`` asks whether the surface named the field as null;
        calling ``getattr(self, name)`` here would re-enter the derivation that
        is asking, so it reads the record directly.
        """
        return getattr(object.__getattribute__(self, '_record'), name, None)

    def __getattr__(self, name):
        # Reached only for names not found on the wrapper/class (so not geo_point / _meta /
        # DERIVED_FIELDS_IN_SAVE / _record / _geo_point / memo caches). Delegate to the record.
        if name in PatientRecordSurface._TYPE_INCOMPATIBLE_FIELDS:
            return None  # raw PatientRecord type would crash the matcher — see the set's docstring
        record = object.__getattribute__(self, '_record')
        if name in _THERAPY_LINE_NAME_FIELDS:
            # Unknown composition is no evidence, not a regimen named "Unknown" (#5485).
            value = getattr(record, name, None)
            return None if _is_unknown_therapy(value) else value
        if name == 'later_therapies':
            return without_unknown_lines(getattr(record, name, None))
        if name in _FIELD_MAPS:
            # §6 conversion (each map embeds prefer-direct-else-convert + unit coherence).
            return _FIELD_MAPS[name](record)
        if name in _DERIVED_VIA_ATTRIBUTES:
            # Group D: re-run CB's own derivation over this surface (prefer a directly-populated value
            # if PROMOP ever adds one). The adequacy/MIPI fields have no PatientRecord column → None →
            # compute. No recursion: the derivations read input fields, never the derived field itself.
            direct = getattr(record, name, None)
            return direct if direct is not None else getattr(self._attributes_service(), name)
        if name == 'progression':
            # CB computes progression in PatientInfo.save (not attributes_service); PROMOP has the
            # column but leaves it NULL. Prefer a populated value, else replicate CB's save logic.
            direct = getattr(record, 'progression', None)
            return direct if direct is not None else _derive_progression(self)
        try:
            return getattr(record, name)
        except AttributeError:
            # A field the matcher's vocabulary (CB PatientInfo) knows but PatientRecord does not
            # carry (a CB-only field) reads as None = "unknown" — the interim-unknown semantics the
            # matcher already handles for a blank field. A missing PRIVATE name (`_...`) re-raises,
            # so the matcher's `hasattr(patient, '_cache')` memo probes stay honest.
            if name.startswith('_'):
                raise
            return None

    def _attributes_service(self):
        # CB's derivation engine, run over THIS surface (its input reads resolve to the record + §6
        # maps). Memoized per request so MIPI/adequacy compute once. Built lazily via __dict__ (not
        # getattr) to avoid re-entering __getattr__.
        svc = self.__dict__.get('_attrs_svc')
        if svc is None:
            factory = object.__getattribute__(self, '_attributes_factory')
            if factory is None:
                from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
                factory = PatientInfoAttributes
            svc = factory(self)
            object.__setattr__(self, '_attrs_svc', svc)
        return svc

    def __setattr__(self, name, value):
        # The matcher memoizes private caches on the patient object; keep them on this transient
        # wrapper rather than mutating the underlying PatientRecord.
        object.__setattr__(self, name, value)

    def __repr__(self):
        return f'<PatientRecordSurface person_id={getattr(self._record, "person_id", None)!r}>'
