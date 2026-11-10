"""PROMOP → EXACT PatientInfo adapter.

Maps a flat PROMOP `patient_info` row (whether fetched via the management
command's psql path or the HTTP `/api/patient-info/{id}/` endpoint) onto
the stateless `PatientInfo` Python class EXACT uses for matching.

Two-stage pipeline:

1. `normalize_promop_row(row)` — normalizes raw PROMOP display strings
   into EXACT's internal code values (receptor status aliases, TNM
   stripping, therapy line outcomes, lab unit fallbacks, etc.). Idempotent.
2. `build_patient_info_from_promop_row(row)` — strips source-only columns,
   delegates to `_build_in_memory` for the snake_case→PatientInfo
   construction + `normalize_patient_info()` derived-field computation.

Extracted from `trials/management/commands/search_trials_for_patients.py`
(#102) so the HTTP resolver in `resolve.py` can reuse the same mapping
without depending on the management command. The management command
re-exports the private aliases for backward compat with existing tests.
"""
import json
import logging
import re
from datetime import date
from decimal import Decimal, DecimalException
from functools import lru_cache
from math import isfinite

from exact_matching.patient_record import receptor_status_code
from rest_framework.exceptions import APIException
from trials.services.patient_vocabulary import PatientVocabularyUnavailable, catalog_rows
from trials.services.therapy_match_profile import omop_therapy_enabled


logger = logging.getLogger(__name__)


class UnsupportedPatientVocabulary(APIException):
    status_code = 422
    default_code = 'unsupported_patient_vocabulary'
    default_detail = {
        'code': default_code,
        'detail': 'Unsupported or contradictory marker assessment. No matching results were produced.',
    }


@lru_cache(maxsize=32)
def _build_code_lookup(model_name):
    """Load only the requested reference, through the host's DB router.

    Keep the model-keyed return shape for management-command compatibility.
    Unrelated patient fields must not depend on every catalog being installed.
    """
    from trials.models import (
        Therapy, TherapyComponent,
        Her2Status, HrStatus, HrdStatus,
        HistologicType, EstrogenReceptorStatus, ProgesteroneReceptorStatus,
        Ethnicity, PlannedTherapy, ConcomitantMedication, CytogenicMarker, MolecularMarker,
        MutationGene, MutationCode, MutationOrigin, MutationInterpretation,
        TumorStage, NodesStage, DistantMetastasisStage, StagingModality,
        BinetStage, ProteinExpression, RichterTransformation, TumorBurden,
        PreExistingConditionCategory, StemCellTransplant,
    )

    all_models = [
        Therapy, TherapyComponent,
        Her2Status, HrStatus, HrdStatus,
        HistologicType, EstrogenReceptorStatus, ProgesteroneReceptorStatus,
        Ethnicity, PlannedTherapy, ConcomitantMedication, CytogenicMarker, MolecularMarker,
        MutationGene, MutationCode, MutationOrigin, MutationInterpretation,
        TumorStage, NodesStage, DistantMetastasisStage, StagingModality,
        BinetStage, ProteinExpression, RichterTransformation, TumorBurden,
        PreExistingConditionCategory, StemCellTransplant,
    ]

    # Don't hardcode `.using('trials')` — the `TrialsDatabaseRouter`
    # already routes trials-app reads to the 'trials' alias when split-DB
    # is configured, and to 'default' otherwise. The previous explicit
    # `.using('trials')` broke single-DB deployments (and the local dev
    # harness) with `ConnectionDoesNotExist` because the alias doesn't
    # exist there.
    if model_name == '_therapy':
        return {'_therapy': {
            **_build_code_lookup('TherapyComponent')['TherapyComponent'],
            **_build_code_lookup('Therapy')['Therapy'],
        }}
    models = {model.__name__: model for model in all_models}
    model = models[model_name]
    model_map = {}
    rows = catalog_rows(model)
    for row in rows:
        for key in (row['title'], row['code']):
            key = key.strip().casefold()
            if key in model_map and model_map[key] != row['code']:
                raise PatientVocabularyUnavailable()
            model_map[key] = row['code']
    aliases = {
        'HrdStatus': {'positive': 'hrd_positive', 'negative': 'hrd_negative'},
    }
    model_map.update(aliases.get(model_name, {}))
    return {model_name: model_map}


def resolve_code(display: str, model_name: str) -> str | None:
    """Resolve a PROMOP display string to an EXACT code for the given model.

    Tries an exact (case-insensitive) title match first, then strips trailing
    parenthetical groups one-by-one (PROMOP format: "Title (brand) (generic)").
    Returns None if no match is found — the field is then treated as unknown
    (potential) by the matcher rather than silently skipping eligibility checks.
    """
    if not display or not display.strip():
        return None
    lookup = _build_code_lookup(model_name)
    model_map = lookup.get(model_name, {})
    s = display.strip()
    while s:
        code = model_map.get(s.casefold())
        if code:
            return code
        s2 = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
        if not s2 or s2 == s:
            break
        s = s2
    return None


def resolve_therapy_code(display: str) -> str | None:
    """Map a PROMOP therapy display string to an EXACT Therapy (or TherapyComponent) code.

    Uses the combined Therapy+TherapyComponent title map so that both regimen-level
    and component-level PROMOP values resolve correctly. Therapy codes take
    priority over component codes when titles collide.
    """
    if not display or not display.strip():
        return None
    therapy_map = _build_code_lookup('_therapy')['_therapy']
    s = display.strip()
    while s:
        code = therapy_map.get(s.casefold())
        if code:
            return code
        s2 = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
        if not s2 or s2 == s:
            break
        s = s2
    return None


#: The other direction, for the subform that edits a ULN input: the select is
#: seeded from the record, so it carries the label PROMOP's `race` options use
#: rather than EXACT's code. Keep this beside the forward map so the two cannot
#: drift apart silently.
RACE_CODE_TO_PROMOP_LABEL = {
    'caucasian_or_european': 'White',
    'african_or_black': 'Black or African American',
    'asian': 'Asian',
    'native_american': 'American Indian or Alaska Native',
    'other': 'Other',
}

#: EXACT's `ethnicity` field holds RACE categories (#4447), while PROMOP stores
#: the OMOP race label in `race` and OMOP ethnicity in `ethnicity`. Only these
#: reviewed race labels map to an EXACT code. OMOP ethnicity labels (Hispanic
#: or Latino / Not Hispanic or Latino), Unknown, and the categories EXACT has no
#: code for (Native Hawaiian / Other Pacific Islander) stay unknown rather than
#: being coerced into "Other" (#5556). Values may be labels, CB legacy titles or
#: already-normalised codes; all three spellings normalise to the same key.
_RACE_LABEL_TO_CODE = {
    'caucasian european': 'caucasian_or_european',
    'caucasian or european': 'caucasian_or_european',
    'caucasian': 'caucasian_or_european',
    'european': 'caucasian_or_european',
    'white': 'caucasian_or_european',
    'white caucasian': 'caucasian_or_european',
    # Legacy PROMOP alias "Caucasian/White".
    'caucasian white': 'caucasian_or_european',

    'african black': 'african_or_black',
    'african or black': 'african_or_black',
    'african': 'african_or_black',
    'black': 'african_or_black',
    'black or african american': 'african_or_black',
    'african american': 'african_or_black',
    # Legacy PROMOP alias "Black/African-American".
    'black african american': 'african_or_black',

    'asian': 'asian',
    'asian american': 'asian',

    'native american': 'native_american',
    'american indian or alaska native': 'native_american',
    'american indian': 'native_american',
    'alaska native': 'native_american',

    'other': 'other',
    'other race': 'other',
    "other won't say": 'other',
    'other wont say': 'other',
}


def _normalise_race_label(value) -> str:
    return ' '.join(
        str(value or '').strip().casefold()
        .replace('_', ' ').replace('/', ' ').replace('-', ' ').split()
    )


def _race_code(value) -> str | None:
    """Map a reviewed race label to an EXACT Ethnicity code, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _RACE_LABEL_TO_CODE.get(_normalise_race_label(value))


def promop_race_label(value):
    """The label PROMOP's `race` options carry for an EXACT ethnicity value.

    Accepts a code, a CB title or a PROMOP label, because a subform entry is
    seeded from whichever of those the patient context holds. An unrecognised
    value is returned unchanged so the select shows what the record has rather
    than blanking a clinical answer.
    """
    code = _race_code(value)
    if code is None:
        return value
    return RACE_CODE_TO_PROMOP_LABEL.get(code, value)


def _race_code_from_row(row: dict) -> str | None:
    """The race-like value EXACT should use for this PROMOP row.

    `race` is the source. A legacy payload may still carry a race label in
    `ethnicity` while `race` is empty, so map that only in that case. An
    explicit-but-unmapped race stays unknown and does not fall back: falling
    back would answer a different question, and an OMOP ethnicity value is
    never a race.
    """
    race = row.get('race')
    if isinstance(race, str) and race.strip():
        return _race_code(race)
    return _race_code(row.get('ethnicity'))


def resolve_code_csv(display: str, model_name: str) -> str | None:
    """Resolve a comma-separated PROMOP display string to a comma-joined list of codes.

    Used for planned_therapies and concomitant_medications. Marker assessments
    have a stricter all-token boundary below. Items that cannot be resolved here
    are silently dropped; returns None if nothing resolved.
    """
    if not display or not display.strip():
        return None
    codes = [
        c for item in display.split(',')
        if (c := resolve_code(item.strip(), model_name))
    ]
    return ','.join(codes) if codes else None


# ------------------------------------------------------------------
# Columns to skip when building the PatientInfo object.
# These are source-only / legacy / computed fields that do not map
# to EXACT's PatientInfo and would confuse the matching engine.
# ------------------------------------------------------------------
SKIP_COLUMNS = frozenset({
    # PKs / FKs
    'id', 'person_id', 'person',
    # Timestamps
    'created_at', 'updated_at',
    # PII not needed for trial matching
    'email', 'date_of_birth',
    # Computed by EXACT (do not override)
    'bmi',
    # Legacy columns not present in EXACT
    'condition_code_icd_10', 'condition_code_snomed_ct',
    'therapy_lines_count', 'line_of_therapy',
    # Legacy duplicate lab fields (EXACT uses named variants)
    'liver_enzyme_levels', 'serum_bilirubin_level',
    # The positive viral status is translated to EXACT's negated field by
    # `normalize_promop_row`; it has no PatientInfo column of its own.
    'hiv_status', 'hepatitis_b_status', 'hepatitis_c_status',
    # PROMOP-only fields with no direct PatientInfo equivalent
    # (metastatic_status is derived by normalize.py from stage; lymph_node and
    #  androgen_receptor have no EXACT matching criteria)
    'metastasis_status', 'lymph_node_status', 'androgen_receptor_status',
    # PostGIS geography field — source uses lat/lon floats instead
    'geo_point',
    # API-only computed fields
    'patient_name', 'age', 'refractory_status',
    # person-table fields added for normalization — not PatientInfo columns
    'gender_source_value', 'gender_concept_id',
})

# JSON fields that may arrive as strings and need decoding
JSON_FIELDS = frozenset({
    'later_therapies', 'supportive_therapies',
    'genetic_mutations', 'stem_cell_transplant_history',
})


#: PROMOP / SNOMED disease surface names → the title the trial corpus stores.
#: The list filter is ``disease__iexact`` against the corpus value ("breast
#: cancer"), so a raw concept name such as "Malignant tumor of breast" matches
#: nothing at all. Keep this to exact-name aliases; unknown diseases pass through.
DISEASE_ALIASES = {
    'malignant tumor of breast': 'Breast Cancer',
    'malignant tumor of breast (disorder)': 'Breast Cancer',
    'malignant neoplasm of breast': 'Breast Cancer',
    'malignant neoplasm of breast (disorder)': 'Breast Cancer',
    'carcinoma of breast': 'Breast Cancer',
    'carcinoma of breast (disorder)': 'Breast Cancer',
    'primary malignant neoplasm of breast': 'Breast Cancer',
    'malignant neoplasm of female breast': 'Breast Cancer',
}


#: The record stores the POSITIVE viral status; trials ask the negated question
#: ("No HIV required"). PROMOP removed the `no_*` columns in #2119, so EXACT
#: reading them found its own class default (`True`) — a confirmed "does not
#: have HIV" for every patient, including one with a positive status (#5663).
#:
#: The negation is derived here, at the vocabulary boundary. The same pair is
#: what the descriptor names for an editor (`kind: negated`), and the client
#: crosses the polarity on display and on save; this module only owns the read
#: side. Keep the pairs identical on both sides — the read-side table in
#: `tests/services/patient_info/test_negated_viral_status.py` pins it.
PROMOP_NEGATED_FIELDS = {
    'no_hiv_status': 'hiv_status',
    'no_hepatitis_b_status': 'hepatitis_b_status',
    'no_hepatitis_c_status': 'hepatitis_c_status',
}


def capture_negated_sources(row: dict) -> dict:
    """Provenance for the canonical positive fields, captured before the pop (#5667).

    `normalize_promop_row` pops the positive key and `_build_in_memory` strips
    nulls, and after those two steps "the field was answered null" and "the
    field was absent" have the same shape. The negated row cannot recover the
    difference from `uvalue`, so it is recorded here, at the raw boundary.

    Availability is KEY PRESENCE, not truthiness: `{'hiv_status': None}` is an
    answer of "unknown", while a row without the key says the context never
    carried it. A negated key alone proves nothing about the positive one. The
    value is the RAW answer, not the inverted display value.
    """
    out = {}
    for positive in PROMOP_NEGATED_FIELDS.values():
        entry = {
            'upatientField': positive,
            'type': 'boolean',
            'available': positive in row,
        }
        if positive in row:
            entry['value'] = row[positive]
        out[positive] = entry
    return out

_TRUE_ANSWERS = frozenset({'yes', 'true', 'positive', '1'})
_FALSE_ANSWERS = frozenset({'no', 'false', 'negative', '0'})


def _boolean_answer(value):
    """A stored boolean, 0/1, or a yes/no label; anything else is unknown.

    Deliberately not `bool(value)`: `"False"` is a truthy string, and inventing
    an answer on an eligibility gate is worse than leaving it unknown.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        label = value.strip().casefold()
        if label in _TRUE_ANSWERS:
            return True
        if label in _FALSE_ANSWERS:
            return False
    return None


REFRACTORY_MAP = {
    'Responsive': 'notRefractory',
    'Stable': 'notRefractory',
    # PROMOP doesn't distinguish primary/secondary/multi-refractory — best-guess mapping.
    'Refractory': 'primaryRefractory',
    'Unknown': None,
}

# PROMOP stores full-text labels; EXACT's _normalize_treatment_refractory_status
# compares against abbreviated IDs ('PD', 'SD', 'MRD', …).
OUTCOME_MAP = {
    'Complete Response': 'CR',
    'Complete Response (CR)': 'CR',
    'Stringent Complete Response (sCR)': 'sCR',
    'Very Good Partial Response (VGPR)': 'VGPR',
    'Partial Response': 'PR',
    'Partial Response (PR)': 'PR',
    'Minimal Residual Disease (MRD) Negativity': 'MRD',
    'Stable Disease (SD)': 'SD',
    'Progressive Disease': 'PD',
    'Progressive Disease (PD)': 'PD',
    'Unknown': None,
}


# CB's PatientRecord read seams (trials/matching/promop_surface.py). Keep each
# fallback's value and unit together; direct zero readings are real answers.
_LAB_FALLBACKS = (
    ('platelet_count', 'platelet_count_thousand_per_ul', 'CELLS/UL', 1000),
    ('hemoglobin_level', 'hemoglobin_g_dl', 'G/DL', 1),
    ('lactate_dehydrogenase_level', 'ldh_u_l', None, 1),
    # Patient/lab-specific LDH upper limit of normal (#631, CB #4512).
    ('lactate_dehydrogenase_level_uln', 'ldh_upper_limit_normal', None, 1),
    ('liver_enzyme_levels_alt', 'alt_u_l', None, 1),
    ('liver_enzyme_levels_ast', 'ast_u_l', None, 1),
    ('liver_enzyme_levels_alp', 'alkaline_phosphatase_u_l', None, 1),
    ('creatinine_clearance_rate', 'creatinine_clearance_ml_min', None, 1),
    ('estimated_glomerular_filtration_rate', 'egfr_ml_min_173m2', None, 1),
    ('serum_calcium_level', 'serum_calcium_mg_dl', 'MG/DL', 1),
    ('serum_creatinine_level', 'serum_creatinine_mg_dl', 'MG/DL', 1),
    ('serum_bilirubin_level_total', 'bilirubin_total_mg_dl', 'MG/DL', 1),
    ('pd_l1_tumor_cels', 'pd_l1_tumor_cells', None, 1),
)

# Counts whose DIRECT CB-parity column is an unreliable copy of the canonical
# thousands/µL source, so the canonical value wins whenever it is supplied
# (#627 for WBC, #628 for ANC/ALC). CB's `promop_surface._FIELD_MAPS` documents
# each case: WBC's and ANC's direct columns are co-populated with the unscaled
# 10³/µL number, and ALC's is filled without the ×1000.
_CANONICAL_WINS = (
    ('white_blood_cell_count', 'wbc_count_thousand_per_ul', 'CELLS/UL', 1000),
    ('absolute_neutrophile_count', 'anc_thousand_per_ul', 'CELLS/UL', 1000),
    ('absolute_lymphocyte_count', 'alc_thousand_per_ul', None, 1000),
    # LDH is the second pair PROMOP declares as an alias of a canonical column
    # (`ldh_u_l`), so the mirror must not win when both are present. Ported
    # from cb-like-trials #557; see the comment on `_normalize_canonical_labs`.
    ('lactate_dehydrogenase_level', 'ldh_u_l', None, 1),
)


def _canonical_lab_value(value, scale):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value)) * scale
        # DRF serializes Decimals through float. A Decimal can be finite yet
        # overflow that wire representation and make strict JSON rendering fail.
        return value if value.is_finite() and isfinite(value) else None
    except (DecimalException, ValueError, OverflowError):
        return None


def _is_blank_text(value):
    return isinstance(value, str) and not value.strip()


#: PROMOP's canonical cytogenetic vocabulary -> CB's `CytogenicMarker.code`.
#: The two overlap only on `hyperdiploidy`, and CB's matcher, its trial
#: `cytogenic_markers_required/excluded` columns and the TP53 / high-risk MCL
#: derivations all speak CB's codes, so a raw PROMOP string never matches
#: (cancerbot#5483, exact#590). Mirrors CB's `promop_surface.CYTOGENETIC_MARKERS_TO_CB`.
_CYTOGENETIC_MARKERS_TO_CB = {
    'del17p': 'del17p13',
    't(4;14)': 't414',
    't(11;14)': 't1114',
    't(14;16)': 't1416',
    '1q_gain': '1q21Amplification',
    '1q_amp': '1q21Amplification',
    'hyperdiploidy': 'hyperdiploidy',
    'MYC rearrangement': 'mycRearrangements',
}


def _cytogenetic_markers(value):
    return _marker_assessment(value, 'CytogenicMarker', _CYTOGENETIC_MARKERS_TO_CB)


def _cytogenetic_markers_with_legacy_titles(value):
    """Backward-compatible spelling; both paths use the current CB axis."""
    return _cytogenetic_markers(value)


#: One item of PROMOP's `molecular_markers` summary: `"{feature or GENE}: {variant}"`,
#: joined with `"; "` (promop `patient_record_service.py`, present findings only).
#: The name has no colon or comma; the variant may be empty or carry brackets.
_PROMOP_FINDING_ITEM = re.compile(r'^[^:,]+:')
_TP53_EVIDENCE = re.compile(r'tp53|17p|del\(17|chromosome 17', re.IGNORECASE)


def _promop_summary_items(value):
    """`value` split on `;` outside brackets: `t(11;14)` stays one item."""
    items, start, depth = [], 0, 0
    for index, char in enumerate(value):
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth = max(depth - 1, 0)
        elif char == ';' and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    items.append(value[start:].strip())
    return items


def _is_promop_findings_summary(value):
    """Whether `value` is PROMOP's display summary of genomic findings (#723).

    Every `;`-separated item (outside brackets) must read `name: variant`, with
    a non-empty name free of commas. No marker code or title contains a colon,
    so a real code list never matches; titles such as `t(11;14) Translocation`
    hold a semicolon but no colon, and do not either.

    A shape test is a stopgap by nature (#723 reads the structured findings).
    It errs safe both ways: an unrecognised summary still gets the strict 422,
    and a recognised one only becomes unknown.
    """
    if not isinstance(value, str) or ':' not in value:
        return False
    items = _promop_summary_items(value)
    return all(items) and all(
        _PROMOP_FINDING_ITEM.match(item) and item.split(':', 1)[0].strip()
        for item in items
    )


def _summary_names_tp53(value):
    """Whether the summary may carry TP53-disruption evidence.

    The whole text, not just the item names: an unclosed bracket can swallow
    a later item, and chromosome-17 losses are spelled several ways. Matching
    too much only turns PROMOP's False into unknown, which is safe.
    """
    return bool(_TP53_EVIDENCE.search(value))


def _row_has_tp53_surrogate(row):
    """Whether the row carries the derivation's other TP53-positive inputs.

    Mirrors `PatientInfoAttributes.tp53_disruption`: del17p13 in the (already
    translated) cytogenetic markers, or p53 IHC of 50% or more.
    """
    cytogenic = row.get('cytogenic_markers')
    if isinstance(cytogenic, str):
        cytogenic = cytogenic.split(',')
    if isinstance(cytogenic, (list, tuple)) and 'del17p13' in [
        str(code).strip() for code in cytogenic
    ]:
        return True
    try:
        return float(row.get('p53_ihc')) >= 50
    except (TypeError, ValueError):
        return False


def _marker_assessment(value, model_name, aliases=None):
    """Resolve every token, never publish a successfully mapped subset.

    This legacy summary cannot represent per-marker completeness (#660).
    Refuse unsupported assessments rather than inventing negative evidence.
    Only exact codes/titles are accepted; removing a parenthetical qualifier
    would change clinical meaning (e.g. strict complex-karyotype criteria).
    """
    if value is None or value == '':
        return ''
    if not isinstance(value, str):
        raise UnsupportedPatientVocabulary()
    text = value.strip()
    if not text or text.casefold() in ('unknown', 'unknown/other'):
        return ''
    if text.casefold() == 'none':
        return 'none'
    # Split CSV only outside parentheses, preserving literal marker titles.
    tokens, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise UnsupportedPatientVocabulary()
        elif char == ',' and depth == 0:
            tokens.append(text[start:index].strip())
            start = index + 1
    tokens.append(text[start:].strip())
    if depth or any(not token or token.casefold() in ('none', 'unknown', 'unknown/other')
                    for token in tokens):
        raise UnsupportedPatientVocabulary()
    model_map = _build_code_lookup(model_name)[model_name]
    codes = []
    for token in tokens:
        source = (aliases or {}).get(token, token)
        code = model_map.get(source.casefold())
        if not code:
            raise UnsupportedPatientVocabulary()
        if code not in codes:
            codes.append(code)
    return ','.join(codes)


def _normalize_canonical_labs(row):
    # These counts' populated legacy aliases can themselves be thousands/µL
    # (WBC, ANC) or were filled without the ×1000 (ALC). When the canonical
    # source is supplied, use it rather than trusting that alias.
    # Keep EXACT's per-L matcher contract unchanged: the paired CELLS/UL unit
    # lets get_value() perform the conversion to EXACT's configured basis.
    #
    # An empty string is how a form spells "no value", not a canonical
    # reading: it must not delete a direct value that was already usable.
    # Any other supplied canonical value still wins even when unparseable —
    # the alias is then untrustworthy (it may be the 1000x-low copy), so the
    # honest answer is unknown rather than a number that could be 10^3 wrong.
    for target, source, unit, scale in _CANONICAL_WINS:
        canonical = row.get(source)
        if canonical is None or _is_blank_text(canonical):
            continue
        row[target] = _canonical_lab_value(canonical, scale)
        if unit is not None:
            row[f'{target}_units'] = unit
    for target, source, unit, scale in _LAB_FALLBACKS:
        direct = row.get(target)
        missing_direct = direct is None or _is_blank_text(direct)
        if missing_direct and row.get(source) is not None and not _is_blank_text(row.get(source)):
            row[target] = _canonical_lab_value(row[source], scale)
            if unit is not None:
                row[f'{target}_units'] = unit


def normalize_promop_row(row: dict) -> dict:
    """Normalize a raw PROMOP patient_info row to EXACT's internal value format.

    Called before _build_in_memory so all downstream filtering sees the right
    code values. Transformations are idempotent — already-normalized values
    pass through unchanged.
    """
    # ── Disease name — PROMOP sends the raw SNOMED concept name ────────
    disease = row.get('disease')
    if isinstance(disease, str):
        row['disease'] = DISEASE_ALIASES.get(
            disease.strip().lower(), disease.strip()
        )

    # Provenance for the negated rows (#5667), recorded before the positive
    # keys are popped below. Kept if a previous pass already recorded it: on
    # the second call the raw keys are gone, and recapturing would turn
    # "answered null" into "never in the context". This also carries the
    # entries through `/normalize-promop-row/`, whose response the federated
    # host sends back as its inline payload.
    if '_negated_sources' not in row:
        sources = capture_negated_sources(row)
        # Only when the context actually carried one of them: an all-absent
        # block says nothing a client cannot read from the ordinary absence of
        # the row, and `/normalize-promop-row/` should not grow a key for every
        # body it is handed.
        if any(entry['available'] for entry in sources.values()):
            row['_negated_sources'] = sources

    # PROMOP owns the source name; translate before the DTO drops unknown keys.
    # Explicit null also wins over a stale internal alias supplied alongside it.
    if 'sct_date' in row:
        row['stem_cell_transplant_date'] = row['sct_date']

    # ── Viral status: the record stores the positive answer ─────────────
    # The trial text is "No Hepatitis B required", and that is what the row
    # asks; `no_hiv_status` is the negated view of `hiv_status`, not a column.
    # Popping the positive field keeps this idempotent: a second call has
    # nothing left to invert. An unreadable or missing answer stays None —
    # "unknown" is not "No".
    for negated, positive in PROMOP_NEGATED_FIELDS.items():
        if positive not in row:
            continue
        answer = _boolean_answer(row.pop(positive))
        row[negated] = None if answer is None else not answer

    # ── Receptor statuses ──────────────────────────────────────────────
    # Reuse package semantics: Positive supplies no expression level, and
    # Equivocal/Borderline are not an asserted low-expression result.
    for _field, _kind, _model in [
        ('her2_status', 'her2', 'Her2Status'),
        ('estrogen_receptor_status', 'er', 'EstrogenReceptorStatus'),
        ('progesterone_receptor_status', 'pr', 'ProgesteroneReceptorStatus'),
        ('hr_status', 'hr', 'HrStatus'),
    ]:
        val = row.get(_field)
        if isinstance(val, str):
            code = receptor_status_code(val, _kind)
            if code is None and val.strip().casefold() not in (
                '', 'equivocal', 'borderline', 'unknown', 'indeterminate',
            ):
                # Older EXACT payloads may carry an exact catalog title.
                title_code = _build_code_lookup(_model)[_model].get(val.strip().casefold())
                code = receptor_status_code(title_code, _kind)
            row[_field] = code
    if isinstance(row.get('hrd_status'), str):
        row['hrd_status'] = resolve_code(row['hrd_status'], 'HrdStatus')

    # ── TNM staging — PROMOP stores full concept names, EXACT expects short codes ──
    # e.g. 'T1: Invasive Tumor ≤ 2 cm' → 't1', 'M0(i+): ...' → 'm0(i_plus)'
    for _tnm in ('tumor_stage', 'nodes_stage', 'distant_metastasis_stage'):
        val = row.get(_tnm)
        if isinstance(val, str) and ':' in val:
            code = val.split(':')[0].strip().lower().replace('(i+)', '(i_plus)')
            row[_tnm] = code

    # ── Histologic type ────────────────────────────────────────────────
    val = row.get('histologic_type')
    if isinstance(val, str):
        row['histologic_type'] = resolve_code(val, 'HistologicType')

    # ── Race — PROMOP keeps race and ethnicity apart ───────────────────
    # EXACT's `ethnicity` field holds race categories (#4447), while PROMOP
    # stores the OMOP race label in `race` and OMOP ethnicity in `ethnicity`.
    # Read the race source here; an OMOP ethnicity answer is never translated
    # into a race (#5556). See `_race_code_from_row` for the fallback rule.
    # Only write the field when the payload named one of the sources: a row
    # without either stays byte-for-byte unchanged for older callers.
    if 'race' in row or 'ethnicity' in row:
        row['ethnicity'] = _race_code_from_row(row)

    # ── Cytogenetic markers — PROMOP's renamed column and vocabulary ────
    # PROMOP's column is `cytogenetic_markers` (migration 0222); EXACT's field
    # keeps CB's older `cytogenic_markers` spelling. The new name wins, the old
    # one is still read for older payloads. Values are translated into CB's
    # codes here, so the generic title resolver below must not touch this field.
    canonical_value = row.get('cytogenetic_markers')
    legacy_value = row.get('cytogenic_markers')
    if canonical_value is not None:
        # The new name wins whenever it carries a value, INCLUDING a blank one:
        # a blank canonical cell is "unknown", and it must not leave a stale
        # value from the old name active (CB reads the new name the same way).
        row['cytogenic_markers'] = _cytogenetic_markers(canonical_value)
    elif legacy_value is not None:
        # Either spelling may carry PROMOP's canonical vocabulary, but an older
        # payload may also hold EXACT's display titles; keep both working.
        row['cytogenic_markers'] = _cytogenetic_markers_with_legacy_titles(legacy_value)
    row.pop('cytogenetic_markers', None)

    # ── Multi-value code fields — resolve CSV display names to codes ───
    # These fields store comma-separated display names in PROMOP but EXACT
    # expects comma-separated normalized codes for has_any_keys filtering.
    if 'molecular_markers' in row:
        if _is_promop_findings_summary(row['molecular_markers']):
            # Stopgap for #723. PROMOP's own summary of present findings is
            # never a list of marker codes, so the strict check below refused
            # the whole patient (422) for anyone with a finding. Read it as
            # unknown instead: `''` is blank to `is_attr_blank`, so marker
            # criteria and the high-risk MCL codes sourced from it stay
            # unknown (potential), never a confirmed "no". TP53 keeps PROMOP's
            # own `tp53_disruption`. Anything else still goes through the
            # strict check and is refused if unsupported. The real fix reads
            # the structured findings (#723).
            summary = row['molecular_markers']
            row['molecular_markers'] = ''
            # The TP53 derivation reads `tp53Mutation` from these markers and,
            # with no aggregate supplied, answers a confirmed False. The summary
            # just dropped may well name TP53, so without PROMOP's own aggregate
            # the honest answer is unknown: an explicit null, which the adapter
            # keeps and the derivation honours.
            row.setdefault('tp53_disruption', None)
            # PROMOP's False needs a strictly `pathogenic` TP53 finding to turn
            # True, so del(17p) left uninterpreted or a "likely pathogenic" TP53
            # reads False while the summary lists that very finding. The markers
            # used to expose the contradiction (`_tp53_contradiction`); with them
            # dropped, say unknown instead of a confirmed "no". A True stays.
            # Only where nothing else on the row says TP53: with a surrogate
            # (p53 IHC >= 50, del17p13) the derivation would turn a null into a
            # confirmed True, while a kept False lets `_tp53_contradiction`
            # answer unknown, as it did when the markers were still readable.
            if (row['tp53_disruption'] is False and _summary_names_tp53(summary)
                    and not _row_has_tp53_surrogate(row)):
                row['tp53_disruption'] = None
        else:
            row['molecular_markers'] = _marker_assessment(row['molecular_markers'], 'MolecularMarker')
    for _field, _model in [
        ('planned_therapies',     'PlannedTherapy'),
        ('concomitant_medications', 'ConcomitantMedication'),
    ]:
        val = row.get(_field)
        if isinstance(val, str) and val.strip():
            row[_field] = resolve_code_csv(val, _model)

    # ── Staging modality — PROMOP stores title ('c → Clinical'), EXACT expects code ('c') ─
    sm = row.get('staging_modalities')
    if isinstance(sm, str) and ' → ' in sm:
        row['staging_modalities'] = sm.split(' → ')[0].strip()

    # ── Tumor grade: PROMOP IntegerField (1,2,3) → EXACT code ('10','20','30') ─
    # Sub-grades 3A/3B (codes '31','32') cannot be derived from PROMOP — map 3 → '30'.
    # Grade 4 is clinically invalid for FL (WHO grades are 1/2/3A/3B only — see #56
    # / CB a8fd82c2 and #69) so we drop it to None rather than produce an orphan
    # '40' code that has no matching dropdown label.
    tg = row.get('tumor_grade')
    if isinstance(tg, int):
        row['tumor_grade'] = {1: '10', 2: '20', 3: '30'}.get(tg)

    # ── Biopsy grade: PROMOP IntegerField (1,2,3) → EXACT code ('1','2','3') ──────────
    bg = row.get('biopsy_grade')
    if isinstance(bg, int):
        row['biopsy_grade'] = str(bg)

    # ── Stage — strip trailing sub-stage letter (IIA → II, IIIB → III) ─
    stage = row.get('stage')
    if stage:
        row['stage'] = re.sub(r'[A-C]$', '', stage)

    # ── Therapy line outcomes — map full-text labels to abbreviated IDs ──────────
    # Required so _normalize_treatment_refractory_status (which checks 'PD'/'SD'/'MRD')
    # can correctly recompute treatment_refractory_status from outcome fields.
    for _of in ('first_line_outcome', 'second_line_outcome', 'later_outcome'):
        val = row.get(_of)
        if val in OUTCOME_MAP:
            row[_of] = OUTCOME_MAP[val]

    # ── Treatment refractory status ────────────────────────────────────
    if row.get('treatment_refractory_status') in REFRACTORY_MAP:
        row['treatment_refractory_status'] = REFRACTORY_MAP[row['treatment_refractory_status']]

    # ── Genetic mutations — normalize casing / format ──────────────────
    # PROMOP uses key 'mutation' for the variant; EXACT expects 'variant'.
    # Variant value also needs code-format normalization (GeneticMutations loader uses
    # value_to_code: str.replace('>','_').replace(' ','_').lower()).
    mutations = row.get('genetic_mutations')
    if isinstance(mutations, list):
        normalized = []
        for m in mutations:
            if not isinstance(m, dict):
                normalized.append(m)
                continue
            m = dict(m)  # idempotence: don't mutate the caller's dict
            if m.get('gene'):
                m['gene'] = m['gene'].lower()
            if m.get('interpretation'):
                m['interpretation'] = m['interpretation'].lower().replace(' ', '_')
            if m.get('origin'):
                raw_origin = m['origin'].lower()
                m['origin'] = raw_origin if raw_origin in ('somatic', 'germline') else None
            # Rename 'mutation' → 'variant' and normalize to EXACT code format
            if 'mutation' in m and 'variant' not in m:
                raw = m.pop('mutation')
                if isinstance(raw, str):
                    m['variant'] = raw.replace('>', '_').replace(' ', '_').lower()
            elif m.get('variant') and isinstance(m['variant'], str):
                m['variant'] = m['variant'].replace('>', '_').replace(' ', '_').lower()
            normalized.append(m)
        row['genetic_mutations'] = normalized

    # ── Lab value fallbacks (PROMOP uses renamed columns) ─────────────
    _normalize_canonical_labs(row)

    # ── Therapy fields ─────────────────────────────────────────────────────────
    _EMPTY_CID = (None, '', 0, '0')
    if omop_therapy_enabled():
        # OMOP mode: the patient speaks OMOP concept_ids (EXACT does no crosswalk).
        # The therapy-LINE fields map to the trial omop_* columns, so source them
        # from PROMOP's resolved concept_ids: the scalar lines from *_therapy_id,
        # and the later_therapies list from later_therapy_ids (get_user_therapies
        # folds that list into the same code set the omop_* overlap reads, so raw
        # internal codes there would silently never match). A line with no concept
        # becomes None / [] (unknown) rather than an unmatchable internal code.
        for _tf in ('first_line_therapy', 'second_line_therapy', 'later_therapy'):
            # Keep the human name the matcher code replaces; PROMOP only
            # sends `_display` for the first two, and the 3L+ line has none.
            if not row.get(f'{_tf}_display') and row.get(_tf):
                row[f'{_tf}_display'] = row[_tf]
            cid = row.get(f'{_tf}_id')
            row[_tf] = str(cid) if cid not in _EMPTY_CID else None
        later_ids = row.get('later_therapy_ids') or []
        row['later_therapies'] = [
            {'therapy': str(c)} for c in later_ids if c not in _EMPTY_CID
        ]
    else:
        # Legacy mode: resolve PROMOP display strings (e.g. "Anastrozole (Arimidex)")
        # to EXACT normalized codes (e.g. "anastrozole") via the LRU-cached
        # Therapy/TherapyComponent title→code map. Unresolvable values → None so the
        # matcher treats them as unknown (potential), not a skipped exclusion check.
        for _tf in ('first_line_therapy', 'second_line_therapy', 'later_therapy'):
            val = row.get(_tf)
            if isinstance(val, str) and val.strip():
                row[_tf] = resolve_therapy_code(val)

    # JSON list fields expect [{therapy: code, ...}] objects — clear if PROMOP sent
    # raw text. supportive_therapies is NOT concept-remapped above because promop
    # does not emit supportive concept_ids yet (promop#230) — NOT because the profile
    # keeps it legacy: the OMOP profile DOES flip supportive to the omop column, but
    # the patient list stays empty so the filter fail-opens (safe no-op) until
    # promop#230. planned_therapies stays legacy (#230). See #231.
    for _tf in ('supportive_therapies', 'later_therapies'):
        val = row.get(_tf)
        if isinstance(val, str) and not val.strip().startswith('['):
            row[_tf] = None

    # ── Behaviour fields — PROMOP stores presence-of-condition (True = HAS condition)
    # but EXACT/CB use absence-of-condition (True = FREE of condition).  Invert the
    # four fields that have no explicit correct mapping in PROMOP's populate pipeline.
    # no_tobacco_use_status and no_pregnancy_or_lactation_status are excluded: PROMOP
    # already populates them in the correct direction.
    for _bfield in (
        'no_mental_health_disorder_status',
        'no_substance_use_status',
        'no_geographic_exposure_risk',
        'no_concomitant_medication_status',
    ):
        val = row.get(_bfield)
        if isinstance(val, bool):
            row[_bfield] = not val

    # ── Metastatic status — PROMOP column name differs from EXACT's ─────
    # PROMOP: metastasis_status (text) → EXACT: metastatic_status (bool)
    ms = row.get('metastasis_status')
    if ms == 'Positive':
        row['metastatic_status'] = True
    elif ms == 'Negative':
        row['metastatic_status'] = False
    # 'Unknown' → leave unset (PatientInfo default is False, so we don't set it)

    # ── Prior therapy — derive from therapy_lines_count ───────────────
    # PROMOP prior_therapy is binary Yes/No; therapy_lines_count has the detail.
    lines = row.get('therapy_lines_count')
    if lines is not None:
        _lines_map = {0: 'None', 1: 'One line', 2: 'Two lines'}
        row['prior_therapy'] = _lines_map.get(lines, 'More than two lines of therapy')

    # ── Age from date_of_birth ─────────────────────────────────────────
    if not row.get('patient_age') and row.get('date_of_birth'):
        dob = row['date_of_birth']
        if isinstance(dob, date):
            row['patient_age'] = (date.today() - dob).days // 365

    # ── Gender — OMOP concept name / id → EXACT code ───────────────────
    # The PROMOP HTTP path sends the OMOP-standard gender as the concept
    # name ('Female'=8532, 'Male'=8507); EXACT expects 'F'/'M'. Translate
    # unconditionally — the value is already populated, so the empty-gender
    # fallback below never sees it. Also accept a bare concept id arriving in
    # the `gender` field itself (the endpoint may surface either shape, #145);
    # the separate `gender_concept_id` field is still handled by the fallback.
    # Idempotent: an already-coded 'F'/'M' row maps to itself.
    #
    # Unrecognized non-binary concepts ('Unknown'/'Ambiguous'/'Other', or an
    # unmapped id) are blanked to None rather than left as a literal string.
    # The matcher treats a blank gender as unknown (`_match_type_str_value` →
    # 'unknown'), keeping the patient a potential match for gender-gated trials;
    # an un-blanked 'Unknown' would instead read as an active criterion and
    # silently EXCLUDE the patient from every gender-restricted trial (#145).
    # Blanking also lets the empty-gender fallback below recover a value from
    # gender_source_value / gender_concept_id when one is present.
    g = row.get('gender')
    if isinstance(g, str):
        gl = g.strip().lower()
        if gl in ('f', 'female'):
            row['gender'] = 'F'
        elif gl in ('m', 'male'):
            row['gender'] = 'M'
        else:
            row['gender'] = None
    elif g == 8507:
        row['gender'] = 'M'
    elif g == 8532:
        row['gender'] = 'F'
    elif g is not None:
        row['gender'] = None

    # ── Gender from person.gender_source_value (added by _fetch_via_db) ─
    # gender_source_value is typically 'M' / 'F' in OMOP; fallback to concept IDs.
    if not row.get('gender'):
        gsv = row.get('gender_source_value', '')
        if gsv in ('M', 'F'):
            row['gender'] = gsv
        elif gsv and gsv.lower().startswith('m'):
            row['gender'] = 'M'
        elif gsv and gsv.lower().startswith('f'):
            row['gender'] = 'F'
        else:
            gci = row.get('gender_concept_id')
            if gci == 8507:
                row['gender'] = 'M'
            elif gci == 8532:
                row['gender'] = 'F'

    return row


#: Fields a PROMOP record NAMES but has no column for. CB's
#: `PromopMatchingSurface._provided_fields` lists the same two: a patient
#: without the supporting labs is undecided, not "not adequate" (#5483, #621).
PROMOP_NAMED_NULL_FIELDS = ('hepatic_adequacy_status', 'haematological_adequacy_status')


def build_patient_info_from_promop_row(row: dict):
    """Convert a PROMOP patient_info row into an in-memory PatientInfo.

    Uses the same normalization pipeline as the web service
    (`normalize_patient_info`) so all derived fields (BMI, geo_point,
    refractory_status, tp53_disruption, etc.) are computed identically to
    what the API would produce.
    """
    from trials.services.patient_info.resolve import _build_in_memory
    from trials.services.patient_info.resolve import translate_language_capabilities

    row = normalize_promop_row(row)
    # Recorded by `normalize_promop_row` before it popped the positive fields.
    negated_sources = row.pop('_negated_sources', None)
    # Language booleans -> legacy codes/asked and OMOP pairs BEFORE the None-stripping
    # below: an all-NULL row must still drop PROMOP's display string and, on the OMOP
    # path, become [] (asked about nothing), not vanish.
    row = translate_language_capabilities(row)

    # Strip source-only columns; decode any JSON-as-string fields
    cleaned = {}
    for col, val in row.items():
        if col in SKIP_COLUMNS:
            continue
        if val is None and col != 'tp53_disruption':
            continue
        if col in JSON_FIELDS and isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                val = [val] if val else []
        if not val and col in JSON_FIELDS:
            continue
        if isinstance(val, date):
            val = val.isoformat()
        if isinstance(val, Decimal):
            val = float(val)
        cleaned[col] = val

    # _build_in_memory handles snake_case→PatientInfo + normalize_patient_info.
    # The adequacy fields are named even though PROMOP has no column for them,
    # so missing liver/blood labs stay unknown instead of becoming a refusal.
    patient = _build_in_memory(
        cleaned,
        named_null_fields=PROMOP_NAMED_NULL_FIELDS,
        negated_sources=negated_sources,
    )
    # OMOP mode normalises the therapy line to its concept id for matching;
    # the reader-facing name travels beside it as `*_display`. It is not a
    # matching field, so the PatientInfo class has no column for it — carry
    # it on the instance for the detail/subform presentation.
    for field in ('first_line_therapy', 'second_line_therapy', 'later_therapy'):
        setattr(patient, f'{field}_display', cleaned.get(f'{field}_display'))
    return patient
