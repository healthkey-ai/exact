"""CTOMOP → EXACT PatientInfo adapter.

Maps a flat CTOMOP `patient_info` row (whether fetched via the management
command's psql path or the HTTP `/api/patient-info/{id}/` endpoint) onto
the stateless `PatientInfo` Python class EXACT uses for matching.

Two-stage pipeline:

1. `normalize_ctomop_row(row)` — normalizes raw CTOMOP display strings
   into EXACT's internal code values (receptor status aliases, TNM
   stripping, therapy line outcomes, lab unit fallbacks, etc.). Idempotent.
2. `build_patient_info_from_ctomop_row(row)` — strips source-only columns,
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
from decimal import Decimal
from functools import lru_cache


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _build_code_lookup():
    """Load title→code mappings for every reference model from the trials DB.

    Returns a dict keyed by model class name, each value being a
    {title_lower: code} dict for that model. lru_cache keeps it in memory
    for the process lifetime (one DB load per command/process).

    Therapy takes priority over TherapyComponent when titles collide so that
    the full eligibility chain (Therapy → components → categories) fires
    correctly from a single therapy code.

    CTOMOP-specific aliases are injected for models whose canonical titles
    differ from the display strings that CTOMOP stores in patient_info.
    """
    from trials.models import (
        Therapy, TherapyComponent,
        Her2Status, HrStatus, HrdStatus,
        HistologicType, EstrogenReceptorStatus, ProgesteroneReceptorStatus,
        Ethnicity, PlannedTherapy, ConcomitantMedication, Marker,
        MutationGene, MutationCode, MutationOrigin, MutationInterpretation,
        TumorStage, NodesStage, DistantMetastasisStage, StagingModality,
        BinetStage, ProteinExpression, RichterTransformation, TumorBurden,
        PreExistingConditionCategory, StemCellTransplant,
    )

    all_models = [
        Therapy, TherapyComponent,
        Her2Status, HrStatus, HrdStatus,
        HistologicType, EstrogenReceptorStatus, ProgesteroneReceptorStatus,
        Ethnicity, PlannedTherapy, ConcomitantMedication, Marker,
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
    lookup = {}
    for model in all_models:
        lookup[model.__name__] = {
            row['title'].lower().strip(): row['code']
            for row in model.objects.values('title', 'code')
        }

    # Therapy overrides TherapyComponent when both share the same title so
    # that the component→category chain resolves via a single Therapy code.
    therapy_map = {**lookup['TherapyComponent'], **lookup['Therapy']}
    lookup['_therapy'] = therapy_map

    # ── CTOMOP-specific aliases ──────────────────────────────────────────
    # CTOMOP stores display strings that differ from EXACT's canonical titles.
    # Add aliases so the resolver can map them without touching the DB data.

    # Her2Status: CTOMOP sends "Negative" / "Positive" / "Equivocal";
    # titles in DB are "HER2-" / "HER2+" / "HER2 low".
    lookup['Her2Status'].update({
        'negative':  'her2_minus',
        'positive':  'her2_plus',
        'equivocal': 'her2_low',   # IHC 2+ / equivocal → low expression
    })

    # EstrogenReceptorStatus: CTOMOP sends "Positive" / "Negative" / "Borderline".
    # Business rule: Positive → hi_exp subtype, Borderline → low_exp subtype.
    lookup['EstrogenReceptorStatus'].update({
        'positive':   'er_plus_with_hi_exp',
        'negative':   'er_minus',
        'borderline': 'er_plus_with_low_exp',
    })

    # ProgesteroneReceptorStatus: same convention as ER.
    lookup['ProgesteroneReceptorStatus'].update({
        'positive':   'pr_plus_with_hi_exp',
        'negative':   'pr_minus',
        'borderline': 'pr_plus_with_low_exp',
    })

    # HrStatus / HrdStatus: simple positive / negative labels.
    lookup['HrStatus'].update({'positive': 'hr_plus', 'negative': 'hr_minus'})
    lookup['HrdStatus'].update({'positive': 'hrd_positive', 'negative': 'hrd_negative'})

    # Ethnicity: CTOMOP sends US-style census labels; Hispanic/Latino has no
    # direct EXACT equivalent — map to 'other'.
    lookup['Ethnicity'].update({
        'caucasian/white':           'caucasian_or_european',
        'white':                     'caucasian_or_european',
        'black/african-american':    'african_or_black',
        'black or african american': 'african_or_black',
        'hispanic or latino':        'other',
        'hispanic/latino':           'other',
    })

    return lookup


def resolve_code(display: str, model_name: str) -> str | None:
    """Resolve a CTOMOP display string to an EXACT code for the given model.

    Tries an exact (case-insensitive) title match first, then strips trailing
    parenthetical groups one-by-one (CTOMOP format: "Title (brand) (generic)").
    Returns None if no match is found — the field is then treated as unknown
    (potential) by the matcher rather than silently skipping eligibility checks.
    """
    if not display or not display.strip():
        return None
    lookup = _build_code_lookup()
    model_map = lookup.get(model_name, {})
    s = display.strip()
    while s:
        code = model_map.get(s.lower())
        if code:
            return code
        s2 = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
        if not s2 or s2 == s:
            break
        s = s2
    return None


def resolve_therapy_code(display: str) -> str | None:
    """Map a CTOMOP therapy display string to an EXACT Therapy (or TherapyComponent) code.

    Uses the combined Therapy+TherapyComponent title map so that both regimen-level
    and component-level CTOMOP values resolve correctly. Therapy codes take
    priority over component codes when titles collide.
    """
    if not display or not display.strip():
        return None
    therapy_map = _build_code_lookup()['_therapy']
    s = display.strip()
    while s:
        code = therapy_map.get(s.lower())
        if code:
            return code
        s2 = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
        if not s2 or s2 == s:
            break
        s = s2
    return None


def resolve_code_csv(display: str, model_name: str) -> str | None:
    """Resolve a comma-separated CTOMOP display string to a comma-joined list of codes.

    Used for multi-value patient fields (cytogenic_markers, molecular_markers,
    planned_therapies, concomitant_medications). Items that cannot be resolved
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
    # Legacy viral flags (EXACT uses no_hiv_status / no_hepatitis_*_status)
    'hiv_status', 'hepatitis_b_status', 'hepatitis_c_status',
    # CTOMOP-only fields with no direct PatientInfo equivalent
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


REFRACTORY_MAP = {
    'Responsive': 'notRefractory',
    'Stable': 'notRefractory',
    # CTOMOP doesn't distinguish primary/secondary/multi-refractory — best-guess mapping.
    'Refractory': 'primaryRefractory',
    'Unknown': None,
}

# CTOMOP stores full-text labels; EXACT's _normalize_treatment_refractory_status
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


def normalize_ctomop_row(row: dict) -> dict:
    """Normalize a raw CTOMOP patient_info row to EXACT's internal value format.

    Called before _build_in_memory so all downstream filtering sees the right
    code values. Transformations are idempotent — already-normalized values
    pass through unchanged.
    """
    # ── Receptor statuses ──────────────────────────────────────────────
    # Resolved via DB-backed LRU map; aliases handle CTOMOP-specific labels
    # (e.g. "Equivocal" → her2_low, "Borderline" → er_plus_with_low_exp).
    # Unknown / empty strings resolve to None → treated as unknown by matcher.
    for _field, _model in [
        ('her2_status',                  'Her2Status'),
        ('estrogen_receptor_status',     'EstrogenReceptorStatus'),
        ('progesterone_receptor_status', 'ProgesteroneReceptorStatus'),
        ('hr_status',                    'HrStatus'),
        ('hrd_status',                   'HrdStatus'),
    ]:
        val = row.get(_field)
        if isinstance(val, str):
            row[_field] = resolve_code(val, _model)

    # ── TNM staging — CTOMOP stores full concept names, EXACT expects short codes ──
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

    # ── Ethnicity — CTOMOP labels → EXACT codes ────────────────────────
    val = row.get('ethnicity')
    if isinstance(val, str):
        row['ethnicity'] = resolve_code(val, 'Ethnicity')

    # ── Multi-value code fields — resolve CSV display names to codes ───
    # These fields store comma-separated display names in CTOMOP but EXACT
    # expects comma-separated normalized codes for has_any_keys filtering.
    for _field, _model in [
        ('cytogenic_markers',     'Marker'),
        ('molecular_markers',     'Marker'),
        ('planned_therapies',     'PlannedTherapy'),
        ('concomitant_medications', 'ConcomitantMedication'),
    ]:
        val = row.get(_field)
        if isinstance(val, str) and val.strip():
            row[_field] = resolve_code_csv(val, _model)

    # ── Staging modality — CTOMOP stores title ('c → Clinical'), EXACT expects code ('c') ─
    sm = row.get('staging_modalities')
    if isinstance(sm, str) and ' → ' in sm:
        row['staging_modalities'] = sm.split(' → ')[0].strip()

    # ── Tumor grade: CTOMOP IntegerField (1,2,3) → EXACT code ('10','20','30') ─
    # Sub-grades 3A/3B (codes '31','32') cannot be derived from CTOMOP — map 3 → '30'.
    # Grade 4 is clinically invalid for FL (WHO grades are 1/2/3A/3B only — see #56
    # / CB a8fd82c2 and #69) so we drop it to None rather than produce an orphan
    # '40' code that has no matching dropdown label.
    tg = row.get('tumor_grade')
    if isinstance(tg, int):
        row['tumor_grade'] = {1: '10', 2: '20', 3: '30'}.get(tg)

    # ── Biopsy grade: CTOMOP IntegerField (1,2,3) → EXACT code ('1','2','3') ──────────
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
    # CTOMOP uses key 'mutation' for the variant; EXACT expects 'variant'.
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

    # ── Lab value fallbacks (CTOMOP uses renamed columns) ─────────────
    if not row.get('hemoglobin_level') and row.get('hemoglobin_g_dl') is not None:
        row['hemoglobin_level'] = row['hemoglobin_g_dl']

    if not row.get('absolute_neutrophile_count') and row.get('anc_thousand_per_ul') is not None:
        row['absolute_neutrophile_count'] = row['anc_thousand_per_ul'] * 1000

    if not row.get('absolute_lymphocyte_count') and row.get('alc_thousand_per_ul') is not None:
        row['absolute_lymphocyte_count'] = row['alc_thousand_per_ul'] * 1000

    if not row.get('lactate_dehydrogenase_level') and row.get('ldh_u_l') is not None:
        row['lactate_dehydrogenase_level'] = row['ldh_u_l']

    # ── Therapy fields — resolve CTOMOP display names to EXACT therapy codes ────
    # CTOMOP stores therapy names as human-readable strings (e.g. "Anastrozole
    # (Arimidex) (Anastrozole)"). EXACT's eligibility filters use normalized codes
    # (e.g. "anastrozole"). resolve_therapy_code() does a title→code lookup via
    # the LRU-cached Therapy/TherapyComponent map, stripping trailing parentheticals
    # to match the EXACT title. Unresolvable values are set to None so they are
    # treated as unknown (potential) by the matcher rather than silently skipping
    # therapy-type exclusion checks.
    for _tf in ('first_line_therapy', 'second_line_therapy', 'later_therapy'):
        val = row.get(_tf)
        if isinstance(val, str) and val.strip():
            row[_tf] = resolve_therapy_code(val)

    # JSON list fields (supportive_therapies, later_therapies) expect
    # [{therapy: code, ...}] objects — clear if CTOMOP sent raw text.
    for _tf in ('supportive_therapies', 'later_therapies'):
        val = row.get(_tf)
        if isinstance(val, str) and not val.strip().startswith('['):
            row[_tf] = None

    # ── Behaviour fields — CTOMOP stores presence-of-condition (True = HAS condition)
    # but EXACT/CB use absence-of-condition (True = FREE of condition).  Invert the
    # four fields that have no explicit correct mapping in CTOMOP's populate pipeline.
    # no_tobacco_use_status and no_pregnancy_or_lactation_status are excluded: CTOMOP
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

    # ── Metastatic status — CTOMOP column name differs from EXACT's ─────
    # CTOMOP: metastasis_status (text) → EXACT: metastatic_status (bool)
    ms = row.get('metastasis_status')
    if ms == 'Positive':
        row['metastatic_status'] = True
    elif ms == 'Negative':
        row['metastatic_status'] = False
    # 'Unknown' → leave unset (PatientInfo default is False, so we don't set it)

    # ── Prior therapy — derive from therapy_lines_count ───────────────
    # CTOMOP prior_therapy is binary Yes/No; therapy_lines_count has the detail.
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
    # The CTOMOP HTTP path sends the OMOP-standard gender as the concept
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


def build_patient_info_from_ctomop_row(row: dict):
    """Convert a CTOMOP patient_info row into an in-memory PatientInfo.

    Uses the same normalization pipeline as the web service
    (`normalize_patient_info`) so all derived fields (BMI, geo_point,
    refractory_status, tp53_disruption, etc.) are computed identically to
    what the API would produce.
    """
    from trials.services.patient_info.resolve import _build_in_memory

    row = normalize_ctomop_row(row)

    # Strip source-only columns; decode any JSON-as-string fields
    cleaned = {}
    for col, val in row.items():
        if col in SKIP_COLUMNS:
            continue
        if val is None:
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

    # _build_in_memory handles snake_case→PatientInfo + normalize_patient_info
    return _build_in_memory(cleaned)
