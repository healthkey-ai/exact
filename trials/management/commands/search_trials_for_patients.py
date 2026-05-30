"""
Management command: search_trials_for_patients

Reads PatientInfo records from an external patient database and matches them
against the trials database using the EXACT matching engine in-process — no
web server required.

Usage
-----
    python manage.py search_trials_for_patients \\
      --source-db-url postgresql://user:pass@host:5432/patients

    Falls back to PATIENT_DATABASE_URL env var if --source-db-url is not given.

Common options
--------------
    --person-ids 1,2,3      # optional: filter to specific person IDs
    --patient-limit 100     # max patients to process (default: all)
    --batch-size 100        # rows per DB fetch
    --limit 50              # max trials to return per patient
    --benefit-weight 25.0
    --patient-burden-weight 25.0
    --risk-weight 25.0
    --distance-penalty-weight 25.0
    --output results.json   # write full JSON output to file
    --format json|csv
    --dry-run               # print first patient's parsed data and exit
"""
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from decimal import Decimal
from functools import lru_cache

from django.core.management.base import BaseCommand
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher


@lru_cache(maxsize=1)
def _build_code_lookup():
    """
    Load title→code mappings for every reference model from the trials DB.

    Returns a dict keyed by model class name, each value being a
    {title_lower: code} dict for that model.  lru_cache keeps it in memory
    for the process lifetime (one DB load per management-command run).

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

    lookup = {}
    for model in all_models:
        lookup[model.__name__] = {
            row['title'].lower().strip(): row['code']
            for row in model.objects.using('trials').values('title', 'code')
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


def _resolve_code(display: str, model_name: str) -> str | None:
    """
    Resolve a CTOMOP display string to an EXACT code for the given model.

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


def _resolve_therapy_code(display: str) -> str | None:
    """
    Map a CTOMOP therapy display string to an EXACT Therapy (or TherapyComponent) code.

    Uses the combined Therapy+TherapyComponent title map so that both regimen-level
    and component-level CTOMOP values resolve correctly.  Therapy codes take
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


def _resolve_code_csv(display: str, model_name: str) -> str | None:
    """
    Resolve a comma-separated CTOMOP display string to a comma-joined list of codes.

    Used for multi-value patient fields (cytogenic_markers, molecular_markers,
    planned_therapies, concomitant_medications).  Items that cannot be resolved
    are silently dropped; returns None if nothing resolved.
    """
    if not display or not display.strip():
        return None
    codes = [
        c for item in display.split(',')
        if (c := _resolve_code(item.strip(), model_name))
    ]
    return ','.join(codes) if codes else None

logger = logging.getLogger(__name__)



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


_REFRACTORY_MAP = {
    'Responsive': 'notRefractory',
    'Stable': 'notRefractory',
    # CTOMOP doesn't distinguish primary/secondary/multi-refractory — best-guess mapping.
    'Refractory': 'primaryRefractory',
    'Unknown': None,
}

# CTOMOP stores full-text labels; EXACT's _normalize_treatment_refractory_status
# compares against abbreviated IDs ('PD', 'SD', 'MRD', …).
_OUTCOME_MAP = {
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


def _normalize_ctomop_row(row: dict) -> dict:
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
            row[_field] = _resolve_code(val, _model)

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
        row['histologic_type'] = _resolve_code(val, 'HistologicType')

    # ── Ethnicity — CTOMOP labels → EXACT codes ────────────────────────
    val = row.get('ethnicity')
    if isinstance(val, str):
        row['ethnicity'] = _resolve_code(val, 'Ethnicity')

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
            row[_field] = _resolve_code_csv(val, _model)

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
        if val in _OUTCOME_MAP:
            row[_of] = _OUTCOME_MAP[val]

    # ── Treatment refractory status ────────────────────────────────────
    if row.get('treatment_refractory_status') in _REFRACTORY_MAP:
        row['treatment_refractory_status'] = _REFRACTORY_MAP[row['treatment_refractory_status']]

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
            m = dict(m)
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
    # (e.g. "anastrozole"). _resolve_therapy_code() does a title→code lookup via
    # the LRU-cached Therapy/TherapyComponent map, stripping trailing parentheticals
    # to match the EXACT title. Unresolvable values are set to None so they are
    # treated as unknown (potential) by the matcher rather than silently skipping
    # therapy-type exclusion checks.
    for _tf in ('first_line_therapy', 'second_line_therapy', 'later_therapy'):
        val = row.get(_tf)
        if isinstance(val, str) and val.strip():
            row[_tf] = _resolve_therapy_code(val)

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


def _build_patient_info(row: dict):
    """Convert a patient_info row into an in-memory PatientInfo.

    Uses the same normalization pipeline as the web service (normalize_patient_info)
    so all derived fields (BMI, geo_point, refractory_status, tp53_disruption, etc.)
    are computed identically to what the API would produce.
    """
    from trials.services.patient_info.resolve import _build_in_memory

    row = _normalize_ctomop_row(row)

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


class Command(BaseCommand):
    help = (
        'Read PatientInfo records from an external patient database and match '
        'them against trials using the EXACT matching engine (in-process).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-db-url',
            type=str,
            default=os.environ.get('PATIENT_DATABASE_URL', ''),
            help='PostgreSQL connection URL for the patient database. '
                 'Falls back to PATIENT_DATABASE_URL env var.',
        )

        # --- Filtering ---
        parser.add_argument(
            '--person-ids',
            type=str,
            default='',
            help='Comma-separated person IDs to process (default: all)',
        )
        parser.add_argument(
            '--patient-limit',
            type=int,
            default=None,
            help='Maximum number of patients to process (default: all). '
                 'Applied before --person-ids filtering.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Rows per DB batch fetch (default: 100)',
        )

        # --- Trial search ---
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Max trials to return per patient (default: 50)',
        )

        # --- Goodness score weights ---
        parser.add_argument(
            '--benefit-weight',
            type=float,
            default=25.0,
            help='Goodness score benefit component weight (default: 25.0)',
        )
        parser.add_argument(
            '--patient-burden-weight',
            type=float,
            default=25.0,
            help='Goodness score patient burden component weight (default: 25.0)',
        )
        parser.add_argument(
            '--risk-weight',
            type=float,
            default=25.0,
            help='Goodness score risk component weight (default: 25.0)',
        )
        parser.add_argument(
            '--distance-penalty-weight',
            type=float,
            default=25.0,
            help='Goodness score distance penalty component weight (default: 25.0)',
        )

        # --- Output ---
        parser.add_argument(
            '--output',
            type=str,
            default='',
            help='Write full results to this file path (default: stdout summary only)',
        )
        parser.add_argument(
            '--format',
            dest='output_format',
            choices=['json', 'csv', 'ground_truth'],
            default='json',
            help='Output format for --output file. '
                 '"ground_truth" writes one row per trial in ground truth CSV format '
                 '(CTOMOP Patient ID, Trial, Eligible/Potential, Suitability Score). '
                 'Default: json',
        )
        parser.add_argument(
            '--ground-truth-csv',
            type=str,
            default='',
            help='Also write results in ground truth CSV format to this path '
                 '(CTOMOP Patient ID, Trial, Eligible/Potential, Suitability Score). '
                 'Can be combined with --output / --format.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the parsed data for the first patient and exit',
        )

        # --- Study preferences / search filters ---
        parser.add_argument(
            '--search-title',
            type=str,
            default='',
            help='Filter trials by title keyword',
        )
        parser.add_argument(
            '--recruitment-status',
            type=str,
            default='',
            help='Filter by recruitment status (e.g. RECRUITING)',
        )
        parser.add_argument(
            '--sponsor',
            type=str,
            default='',
            help='Filter trials by sponsor name',
        )
        parser.add_argument(
            '--register',
            type=str,
            default='',
            help='Filter by trial register (e.g. ClinicalTrials.gov)',
        )
        parser.add_argument(
            '--trial-type',
            type=str,
            default='',
            help='Filter by trial type',
        )
        parser.add_argument(
            '--study-type',
            type=str,
            default='',
            help='Filter by study type',
        )
        parser.add_argument(
            '--study-id',
            type=str,
            default='',
            help='Filter by specific study ID (e.g. NCT number)',
        )
        parser.add_argument(
            '--validated-only',
            action='store_true',
            help='Return only manually validated trials',
        )
        parser.add_argument(
            '--distance',
            type=float,
            default=None,
            help='Maximum distance from patient location to trial site',
        )
        parser.add_argument(
            '--distance-units',
            type=str,
            default='km',
            choices=['km', 'miles'],
            help='Distance units: km or miles (default: km)',
        )
        parser.add_argument(
            '--country',
            type=str,
            default='',
            help='Filter trials to a specific country code (e.g. US, DE)',
        )
        parser.add_argument(
            '--region',
            type=str,
            default='',
            help='Filter trials to a specific region/state',
        )
        parser.add_argument(
            '--postal-code',
            type=str,
            default='',
            help='Filter trials near a postal code',
        )
        parser.add_argument(
            '--last-update',
            type=str,
            default='',
            help='Filter trials updated after this date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--first-enrolment',
            type=str,
            default='',
            help='Filter trials with first enrolment after this date (YYYY-MM-DD)',
        )

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        person_ids = []
        if options['person_ids']:
            person_ids = [int(x.strip()) for x in options['person_ids'].split(',') if x.strip()]

        rows = self._fetch_via_db(options, person_ids)

        if rows is None:
            return  # error already reported

        all_results = []
        processed = 0
        errors = 0

        for row in rows:
            person_id = row.get('person_id')
            disease = row.get('disease') or ''

            if not disease:
                self.stdout.write(f'  Skipping person_id={person_id} (no disease set)')
                continue

            if options['dry_run']:
                self.stdout.write(
                    f'person_id={person_id}, disease={disease}\n'
                    f'Row keys: {list(row.keys())}'
                )
                return

            try:
                result = self._search_trials_direct(
                    row=dict(row),
                    person_id=person_id,
                    disease=disease,
                    limit=options['limit'],
                    benefit_weight=options['benefit_weight'],
                    patient_burden_weight=options['patient_burden_weight'],
                    risk_weight=options['risk_weight'],
                    distance_penalty_weight=options['distance_penalty_weight'],
                    search_title=options['search_title'],
                    recruitment_status=options['recruitment_status'],
                    sponsor=options['sponsor'],
                    register=options['register'],
                    trial_type=options['trial_type'],
                    study_type=options['study_type'],
                    study_id=options['study_id'],
                    validated_only=options['validated_only'],
                    distance=options['distance'],
                    distance_units=options['distance_units'],
                    country=options['country'],
                    region=options['region'],
                    postal_code=options['postal_code'],
                    last_update=options['last_update'],
                    first_enrolment=options['first_enrolment'],
                )
                all_results.append(result)
                self._print_patient_summary(result)
                processed += 1

                if processed % 10 == 0:
                    self.stdout.write(f'  Processed {processed} patients...')

            except Exception:
                errors += 1
                logger.exception('Error searching trials for person_id=%s', person_id)
                self.stderr.write(self.style.ERROR(f'  Error for person_id={person_id}'))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Patients processed: {processed}, Errors: {errors}'
        ))

        if options['output']:
            self._write_output(all_results, options['output'], options['output_format'])
            self.stdout.write(self.style.SUCCESS(f'Results written to: {options["output"]}'))

        if options['ground_truth_csv']:
            self._write_output(all_results, options['ground_truth_csv'], 'ground_truth')
            self.stdout.write(self.style.SUCCESS(f'Ground truth CSV written to: {options["ground_truth_csv"]}'))

    # ------------------------------------------------------------------
    # Source: DB
    # ------------------------------------------------------------------

    def _fetch_via_db(self, options, person_ids):
        """Fetch rows from the patient_info table via psql subprocess.

        Uses psql instead of a direct psycopg2 connection to avoid a
        double-free crash on macOS/conda when two libpq connections are open
        simultaneously (Django's trials DB connection + a second connection).
        """
        source_db_url = options['source_db_url']
        if not source_db_url:
            self.stderr.write(self.style.ERROR(
                'No source DB URL. Use --source-db-url or set PATIENT_DATABASE_URL.'
            ))
            return None

        if not shutil.which('psql'):
            self.stderr.write(self.style.ERROR(
                'psql not found in PATH — required for patient DB queries.'
            ))
            return None

        # Build query — person_ids and patient_limit are integers, safe to inline
        query = '''
            SELECT pi.*, p.gender_source_value, p.gender_concept_id
            FROM patient_info pi
            JOIN person p ON pi.person_id = p.person_id
        '''
        if person_ids:
            ids_str = ', '.join(str(int(pid)) for pid in person_ids)
            query += f' WHERE p.person_id IN ({ids_str})'
        query += ' ORDER BY p.person_id'
        if options.get('patient_limit'):
            query += f' LIMIT {int(options["patient_limit"])}'

        wrapped = f'SELECT row_to_json(t) FROM ({query}) t'
        env = {**os.environ, 'PGSSLMODE': 'require'}

        try:
            result = subprocess.run(
                ['psql', source_db_url, '-t', '--no-psqlrc', '-c', wrapped],
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'psql failed: {e}'))
            return None

        if result.returncode != 0:
            self.stderr.write(self.style.ERROR(f'psql error: {result.stderr.strip()}'))
            return None

        rows = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))

        return rows

    # ------------------------------------------------------------------
    # Direct trial search (in-process, no HTTP)
    # ------------------------------------------------------------------

    def _search_trials_direct(self, row: dict, person_id, disease, limit: int,
                               benefit_weight=25.0, patient_burden_weight=25.0,
                               risk_weight=25.0, distance_penalty_weight=25.0,
                               search_title='', recruitment_status='', sponsor='',
                               register='', trial_type='', study_type='', study_id='',
                               validated_only=False, distance=None, distance_units='km',
                               country='', region='', postal_code='',
                               last_update='', first_enrolment=''):
        """Match trials against a patient row using the EXACT ORM directly.

        Pipeline:
          1. _build_patient_info() → normalize_patient_info()
          2. Trial.objects.filtered_trials() — eligibility queryset
          3. with_goodness_score_optimized() — scoring
          4. UserToTrialAttrMatcher — eligible/potential classification
        """
        from trials.models import Trial
        from trials.services.study_preferences import StudyPreferences

        pi = _build_patient_info(row)

        study_prefs = StudyPreferences(
            search_title=search_title or None,
            sponsor=sponsor or None,
            register=register or None,
            study_id=study_id or None,
            trial_type=trial_type or None,
            study_type=study_type or None,
            recruitment_status=recruitment_status or None,
            country=country or None,
            region=region or None,
            postal_code=postal_code or None,
            distance=distance,
            distance_units=distance_units,
            validated_only=validated_only,
            last_update=last_update or None,
            first_enrolment=first_enrolment or None,
        )

        queryset = Trial.objects.all()
        queryset, _ = queryset.filtered_trials(
            search_options={},
            study_info=study_prefs,
            patient_info=pi,
            add_traces=False,
        )
        queryset = queryset.with_goodness_score_optimized(
            benefit_weight=benefit_weight,
            patient_burden_weight=patient_burden_weight,
            risk_weight=risk_weight,
            distance_penalty_weight=distance_penalty_weight,
            geo_point=pi.geo_point if pi else None,
            recruitment_status=study_prefs.recruitment_status,
        )

        total = queryset.count()
        trials_page = list(queryset[:limit])

        eligible = []
        potential = []
        scores = []
        goodness_scores = []
        trials_out = []

        for trial in trials_page:
            matcher = UserToTrialAttrMatcher(trial=trial, patient_info=pi)
            match_type = matcher.trial_match_status()
            if match_type == 'not_eligible':
                continue
            match_score = matcher.trial_match_score()
            goodness_score = getattr(trial, 'goodness_score', None)
            if match_score is not None:
                scores.append(match_score)
            if goodness_score is not None:
                goodness_scores.append(float(goodness_score))
            trial_data = {
                'studyId': trial.study_id,
                'briefTitle': trial.brief_title,
                'officialTitle': trial.official_title,
                'matchingType': match_type,
                'matchScore': match_score,
                'goodnessScore': goodness_score,
                'recruitmentStatus': trial.recruitment_status,
                'phase': trial.phases,
                'studyType': trial.study_type,
                'sponsor': trial.sponsor_name,
                'link': trial.link,
                'disease': trial.disease,
                'register': trial.register,
            }
            trials_out.append(trial_data)
            if match_type == 'eligible':
                eligible.append(trial_data)
            else:
                potential.append(trial_data)

        return {
            'person_id': person_id,
            'disease': disease,
            'total_trials': total,
            'returned_trials': len(trials_page),
            'eligible_count': len(eligible),
            'potential_count': len(potential),
            'best_match_score': max(scores) if scores else None,
            'best_goodness_score': max(goodness_scores) if goodness_scores else None,
            'eligible_trials': eligible,
            'potential_trials': potential,
            'trials': trials_out,
        }

    def _print_patient_summary(self, result):
        pid = result['person_id']
        disease = result['disease'] or '(unknown)'
        total = result['total_trials']
        eligible = result['eligible_count']
        potential = result['potential_count']
        match_score = result['best_match_score']
        goodness_score = result.get('best_goodness_score')
        match_str = f'{match_score}%' if match_score is not None else 'n/a'
        goodness_str = f'{goodness_score:.1f}' if goodness_score is not None else 'n/a'

        self.stdout.write(
            f'  person_id={pid} [{disease}] '
            f'→ {total} total | {eligible} eligible | {potential} potential '
            f'| best match: {match_str} | best goodness: {goodness_str}'
        )

    def _write_output(self, results, path, fmt):
        if fmt == 'json':
            with open(path, 'w') as f:
                json.dump(results, f, indent=2, default=str)

        elif fmt == 'csv':
            if not results:
                return
            fieldnames = [
                'person_id', 'disease',
                'total_trials', 'eligible_count', 'potential_count',
                'best_match_score', 'best_goodness_score', 'top_trial_ids',
            ]
            with open(path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in results:
                    top_ids = ','.join(
                        t.get('studyId', '') for t in r.get('trials', [])[:5]
                    )
                    writer.writerow({
                        'person_id': r['person_id'],
                        'disease': r['disease'],
                        'total_trials': r['total_trials'],
                        'eligible_count': r['eligible_count'],
                        'potential_count': r['potential_count'],
                        'best_match_score': r['best_match_score'],
                        'best_goodness_score': r.get('best_goodness_score'),
                        'top_trial_ids': top_ids,
                    })

        elif fmt == 'ground_truth':
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'CTOMOP Patient ID', 'Trial',
                    'Eligible/Potential', 'Suitability Score',
                ])
                for r in results:
                    for t in r.get('trials', []):
                        trial_id = t.get('studyId') or ''
                        writer.writerow([
                            r['person_id'],
                            trial_id,
                            t.get('matchingType', ''),
                            int(t['goodnessScore']) if t.get('goodnessScore') is not None else '',
                        ])
