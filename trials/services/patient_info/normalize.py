"""
Pure function to compute derived PatientInfo fields.

Called from resolve_patient_info() when constructing an in-memory instance.
"""
from django.contrib.gis.geos import Point

from trials.services.patient_info.patient_info_flipi_score import PatientInfoFlipyScore
from trials.services.patient_info.patient_info_geo_point import PatientInfoGeoPoint


# The attributes this module computes, and therefore the attributes a stored
# value cannot decide.
#
# It is written down rather than inferred because clients need it and cannot
# see it. EXACT recomputes these on every match from the inputs it was given,
# so a value written into the patient record upstream — by a patient editing
# their profile, by an import — is replaced before it ever reaches the
# matcher. A client that offers an edit box for one of them offers a control
# whose effect is undone by the next request, with no error anywhere: the
# write is accepted, the re-read returns the recomputed value, and the reader
# is left to conclude that nothing happened (EXACT #449).
#
# Note this is NOT the same question as `is_computed_value` in the attribute
# config, which is about presentation and disagrees with this list in both
# directions. Nor is it PROMOP's `writable`, which answers whether the record
# will TAKE the write — it will; this answers whether the write survives.
#
# `test_normalize_recomputed_register` holds it to the module: the set and
# the assignments below are checked against each other, so adding a
# derivation without adding its name here fails the suite.
RECOMPUTED_ATTRIBUTES = frozenset({
    'bmi',
    'bulky_disease_criteria',
    'country',
    'estimated_glomerular_filtration_rate',
    'first_line_date',
    'first_line_outcome',
    'first_line_therapy',
    'flipi_score',
    'geo_point',
    'haematological_adequacy_status',
    'hepatic_adequacy_status',
    'high_risk_mcl_criteria',
    'hr_status',
    'last_treatment',
    'later_date',
    'later_outcome',
    'later_therapies',
    'later_therapy',
    'measurable_disease_imwg',
    'meets_crab',
    'meets_slim',
    'metastatic_status',
    'mipi_c_risk',
    'mipi_risk',
    'postal_code',
    'progression',
    'renal_adequacy_status',
    'second_line_date',
    'second_line_outcome',
    'second_line_therapy',
    'stem_cell_transplant_history',
    'supportive_therapies',
    'supportive_therapy_date',
    'tnbc_status',
    'tp53_disruption',
    'treatment_refractory_status',
})


def normalize_patient_info(pi) -> None:
    """Compute and set all derived fields on a PatientInfo instance in-place.

    Does NOT save to the database. Safe to call on unsaved instances.
    """
    from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
    from trials.services.patient_info.convertors.egfr_calculator import EgfrCalculator

    _normalize_therapy_lines(pi)
    _normalize_treatment_refractory_status(pi)
    _normalize_geo_point(pi)
    _normalize_flipi_score(pi)
    _normalize_tnbc_status(pi)
    _normalize_hr_status(pi)
    _normalize_metastatic_status(pi)
    _normalize_measurable_disease_imwg(pi)
    _normalize_last_treatment(pi)
    _normalize_mcl_derivations(pi)

    attr = PatientInfoAttributes(pi)
    pi.bmi = attr.bmi
    pi.meets_crab = attr.meets_crab
    pi.meets_slim = attr.meets_slim
    if pi.meets_crab is True or pi.meets_slim is True:
        pi.progression = 'active'
    elif pi.meets_crab is False and pi.meets_slim is False and not pi.progression:
        pi.progression = 'smoldering'
    sct = attr.stem_cell_transplant_history_from_therapy_lines
    if sct:
        pi.stem_cell_transplant_history = [sct]
    pi.renal_adequacy_status = attr.renal_adequacy_status
    pi.hepatic_adequacy_status = attr.hepatic_adequacy_status
    pi.haematological_adequacy_status = attr.haematological_adequacy_status
    egfr = EgfrCalculator.call(pi)
    if egfr:
        pi.estimated_glomerular_filtration_rate = egfr
    pi.tp53_disruption = attr.tp53_disruption


# ---------------------------------------------------------------------------
# The overwrite register (#449)
# ---------------------------------------------------------------------------
#
# Which patient values this module writes. Declared here, next to the code
# that does the writing, because the question it answers is asked somewhere
# else entirely: an editing client deciding whether to draw a pencil next to a
# value (`uoverwritten` on an eligibility row). A value EXACT recomputes is
# accepted by PROMOP, saved, and then silently reverted by the next match — so
# "PROMOP will take this write" and "this write will survive" are different
# questions, and only this module knows the second one.
#
# Two flags already looked like the answer and are not. `ureadonly` is set
# from the presence of a subform, nothing more. `is_computed_value` in
# USER_TO_TRIAL_ATTRS_MAPPING is a display flag: `_normalize_mcl_derivations`
# below writes four fields on consecutive lines and only one of them carries
# it, while `meets_gelf` and `meets_lugano` carry it and are plain stored
# BooleanFields nothing derives. Measured against the 133 mapped attributes:
# it flags 12, four of which nothing overwrites, and misses 16 that this
# module does — 20 disagreements either way.
#
# `tests/services/patient_info/test_overwrite_register.py` parses this module
# and fails if the two ever drift — in either direction, so the register can
# neither miss a new write nor keep an entry for one that was removed. That
# test is the only reason this is a register rather than a comment.
#
# SCOPE: this module, which is the derivation stage. Two other stages also
# write patient values and are NOT covered — `ctomop_adapter` derives
# `prior_therapy` and `metastatic_status` from CTOMOP columns on the
# `?person_id=` path, and `resolve._coerce_*` drops malformed items from the
# JSON list fields. Tracked in #471; until it closes, a client reading
# `uoverwritten: null` is being told "the DERIVATION stage leaves this alone",
# which is the whole answer only on the inline-payload path.

#: Written on every call, whatever the caller supplied.
OVERWRITTEN_ALWAYS = frozenset({
    'bmi',
    'haematological_adequacy_status',
    'hepatic_adequacy_status',
    'hr_status',
    'last_treatment',
    'measurable_disease_imwg',
    'meets_crab',
    'meets_slim',
    'metastatic_status',
    'renal_adequacy_status',
    'tnbc_status',
    'tp53_disruption',
    'treatment_refractory_status',
})

#: Written only when the condition holds. The condition is prose because a
#: client shows it to a reader: "overwritten when height and weight are both
#: present" is a different thing to be told than "always".
OVERWRITTEN_WHEN = {
    'bulky_disease_criteria': 'for a mantle cell lymphoma patient',
    'country': 'cleared when the value names no country EXACT can resolve',
    'estimated_glomerular_filtration_rate': (
        'when serum creatinine and age are present and non-zero, the gender is '
        'M or F, and the creatinine units convert'
    ),
    'first_line_date': "cleared when prior therapy is 'None' or blank",
    'first_line_outcome': "cleared when prior therapy is 'None' or blank",
    'first_line_therapy': "cleared when prior therapy is 'None' or blank",
    'flipi_score': (
        'when at least one recognised FLIPI option is selected — age, stage, '
        'hemoglobin, nodalAreas or ldh; anything else scores nothing'
    ),
    'geo_point': (
        'when a country or postal code is supplied, or a non-zero latitude and '
        'longitude (a zero coordinate is dropped — #470)'
    ),
    'high_risk_mcl_criteria': 'for a mantle cell lymphoma patient',
    'later_date': (
        "cleared when prior therapy is 'Two lines', 'One line', 'None' or "
        'blank'
    ),
    'later_outcome': (
        "cleared when prior therapy is 'Two lines', 'One line', 'None' or "
        'blank'
    ),
    'later_therapies': (
        "cleared when prior therapy is 'Two lines', 'One line', 'None' or "
        'blank'
    ),
    'later_therapy': (
        "cleared when prior therapy is 'Two lines', 'One line', 'None' or "
        'blank'
    ),
    'mipi_c_risk': 'for a mantle cell lymphoma patient',
    'mipi_risk': 'for a mantle cell lymphoma patient',
    'postal_code': (
        'cleared when a postal code is supplied without a country, or when the '
        'country and postal code resolve to no point'
    ),
    'progression': (
        'set to active when either CRAB or SLIM is met; set to smoldering when '
        'both are known and unmet and nothing was supplied — an unknown '
        'leaves it alone'
    ),
    'second_line_date': "cleared when prior therapy is 'One line', 'None' or blank",
    'second_line_outcome': "cleared when prior therapy is 'One line', 'None' or blank",
    'second_line_therapy': "cleared when prior therapy is 'One line', 'None' or blank",
    'stem_cell_transplant_history': (
        "when the therapy lines imply a transplant, or when prior therapy is 'None'"
    ),
    'supportive_therapies': "cleared when prior therapy is 'None' or blank",
    'supportive_therapy_date': "cleared when prior therapy is 'None' or blank",
}

#: Every field this module writes, however conditionally.
OVERWRITTEN_FIELDS = OVERWRITTEN_ALWAYS | frozenset(OVERWRITTEN_WHEN)


def _is_computed_on_read(field):
    """Whether `field` is a property recomputed on access, with no setter.

    A different way for a reader's value not to survive, and a worse one: the
    value is not stored at all. `_build_in_memory` filters an inbound payload
    to `_meta.get_fields()`, so a supplied value is dropped before it reaches
    the instance, and `setattr` would raise if it got there.

    Late import: `patient_info` reaches this module through `configs`, so
    resolving it at module load is a cycle.
    """
    from trials.services.patient_info.patient_info import PatientInfo

    attribute = getattr(PatientInfo, field, None)
    return isinstance(attribute, property) and attribute.fset is None


def overwrite_note(field):
    """Whether a value the reader supplies for `field` survives, and how not.

    Three answers, and `None` is the fourth:

        {'when': 'always'}                 this module rewrites it every match
        {'when': 'sometimes', 'condition'} it rewrites it under that condition
        {'when': 'never-stored'}           it is computed on read; there is
                                           nothing to write in the first place
        None                               EXACT leaves it alone

    `never-stored` is not a register entry, because the register is about what
    THIS module writes and these it does not: `abnormal_kappa_lambda_ratio` and
    `meets_meas_or_bone_status` are properties with no column and no setter.
    Review caught them answering `None` — which a client reads as "this value
    is the reader's", over a field an edit cannot reach at all. Same false
    negative #449 exists to remove, one stage further along.

    `None` still does not mean writable. That is PROMOP's question
    (`writable-fields`), it is caller-aware, and the two come apart in both
    directions.
    """
    if field in OVERWRITTEN_ALWAYS:
        return {'when': 'always'}
    condition = OVERWRITTEN_WHEN.get(field)
    if condition is not None:
        return {'when': 'sometimes', 'condition': condition}
    if field and _is_computed_on_read(field):
        return {'when': 'never-stored'}
    return None


# ---------------------------------------------------------------------------
# Individual normalizers
# ---------------------------------------------------------------------------

def _normalize_therapy_lines(pi) -> None:
    """Clear downstream therapy fields when prior_therapy is reduced."""
    if pi.prior_therapy == 'More than two lines of therapy':
        return

    if pi.prior_therapy == 'Two lines':
        pi.later_therapies = []
        pi.later_therapy = None
        pi.later_date = None
        pi.later_outcome = None
        return

    if pi.prior_therapy == 'One line':
        pi.later_therapies = []
        pi.later_therapy = None
        pi.later_date = None
        pi.later_outcome = None
        pi.second_line_therapy = None
        pi.second_line_date = None
        pi.second_line_outcome = None
        return

    if pi.prior_therapy in ('None', '', None):
        pi.later_therapies = []
        pi.later_therapy = None
        pi.later_date = None
        pi.later_outcome = None
        pi.second_line_therapy = None
        pi.second_line_date = None
        pi.second_line_outcome = None
        pi.first_line_therapy = None
        pi.first_line_date = None
        pi.first_line_outcome = None
        pi.supportive_therapies = []
        pi.supportive_therapy_date = None
        if pi.prior_therapy == 'None':
            pi.stem_cell_transplant_history = 'None'


def _normalize_treatment_refractory_status(pi) -> None:
    high_level_outcomes = {'MRD', 'SD', 'PD'}
    refractory_levels = [
        "notRefractory",
        "primaryRefractory",
        "secondaryRefractory",
        "multiRefractory",
    ]

    if pi.prior_therapy == 'None':
        pi.treatment_refractory_status = "notRefractory"
        return

    if (pi.first_line_outcome is None
            and pi.second_line_outcome is None
            and pi.later_outcome is None):
        pi.treatment_refractory_status = None
        return

    level = sum([
        pi.first_line_outcome in high_level_outcomes,
        pi.second_line_outcome in high_level_outcomes,
        pi.later_outcome in high_level_outcomes,
    ])
    pi.treatment_refractory_status = refractory_levels[level]


def _normalize_geo_point(pi) -> None:
    if pi.country or pi.postal_code:
        if pi.country:
            country_code = PatientInfoGeoPoint.country_code_by_country_code_or_name(pi.country)
            if not country_code:
                pi.country = None

            pi.geo_point = PatientInfoGeoPoint.point_by_country_and_postal_code(
                pi.country, pi.postal_code
            )
            if not pi.geo_point:
                pi.postal_code = None
        else:
            pi.postal_code = None
            pi.geo_point = None
    elif pi.longitude and pi.latitude and not (pi.country or pi.postal_code):
        pi.geo_point = Point(pi.longitude, pi.latitude, srid=4326)


def _normalize_flipi_score(pi) -> None:
    score = PatientInfoFlipyScore.scope_by_options(pi.flipi_score_options)
    if score is not None:
        pi.flipi_score = score


def _normalize_tnbc_status(pi) -> None:
    if (pi.estrogen_receptor_status == 'er_minus'
            and pi.progesterone_receptor_status == 'pr_minus'
            and pi.her2_status == 'her2_minus'):
        pi.tnbc_status = True
    else:
        pi.tnbc_status = False


def _normalize_hr_status(pi) -> None:
    er = pi.estrogen_receptor_status
    pr = pi.progesterone_receptor_status

    if er == 'er_plus_with_hi_exp' or pr == 'pr_plus_with_hi_exp':
        pi.hr_status = 'hr_plus_with_hi_exp'
    elif er is None or pr is None:
        pi.hr_status = None
    elif er == 'er_plus' or pr == 'pr_plus':
        pi.hr_status = 'hr_plus'
    elif er == 'er_plus_with_low_exp' or pr == 'pr_plus_with_low_exp':
        pi.hr_status = 'hr_plus_with_low_exp'
    elif er == 'er_minus' and pr == 'pr_minus':
        pi.hr_status = 'hr_minus'
    else:
        pi.hr_status = None


def _normalize_metastatic_status(pi) -> None:
    if str(pi.disease).lower() != 'breast cancer':
        pi.metastatic_status = False
        return
    pi.metastatic_status = pi.stage == 'IV'


def _normalize_measurable_disease_imwg(pi) -> None:
    def serum_m_protein_high():
        # Use `is None` not `not X`: 0 is a real clinical measurement
        # ("no monoclonal protein detected"), not missing data (#81).
        if pi.monoclonal_protein_serum is None:
            return None
        return pi.monoclonal_protein_serum >= 0.5

    def serum_m_urine_high():
        if pi.monoclonal_protein_urine is None:
            return None
        return pi.monoclonal_protein_urine >= 200

    def kappa_lambda_ratio():
        if pi.kappa_flc is None or pi.lambda_flc is None:
            return None
        if float(pi.lambda_flc) == 0:
            return None
        return float(pi.kappa_flc) / float(pi.lambda_flc)

    def kappa_lambda_abnormal_and_high():
        if pi.kappa_flc is None or pi.lambda_flc is None:
            return None
        ratio = kappa_lambda_ratio()
        # Use `is None` not `not ratio`: ratio=0 (kappa=0/lambda>0) is a
        # real clinical signal — well below the 0.26 abnormal threshold —
        # not missing data (#81).
        if ratio is None:
            return False
        if not (ratio < 0.26 or ratio > 1.65):
            return False
        return pi.kappa_flc >= 100 or pi.lambda_flc >= 100

    components = [serum_m_protein_high(), serum_m_urine_high(), kappa_lambda_abnormal_and_high()]
    for component in components:
        if component is True:
            pi.measurable_disease_imwg = True
            return
    # All components None → no relevant lab data, result is unknown.
    # Avoids the "shows No by default" UX bug (#4143 / #4156).
    if all(c is None for c in components):
        pi.measurable_disease_imwg = None
        return
    pi.measurable_disease_imwg = False


def _normalize_last_treatment(pi) -> None:
    pi.last_treatment = pi.later_date or pi.second_line_date or pi.first_line_date


def _normalize_mcl_derivations(pi) -> None:
    """Overwrite caller-supplied MCL risk scores with derived values.

    The PatientInfo field declarations note these are caller-settable for
    forward-compat but always overwritten here (#41 / see
    patient_info.py:215). Skips non-MCL patients so other diseases keep
    whatever defaults / inputs they had.
    """
    # Whitespace/None-tolerant, consistent with PatientInfoAttributes.disease_code
    # (CB #4323): else a padded 'mantle cell lymphoma ' would resolve to disease_code
    # 'MCL' (MCL attrs matched) yet skip derivation here (MCL matched on blank values).
    if (pi.disease or '').strip().lower() != 'mantle cell lymphoma':
        return

    # Late import: PatientInfoAttributes imports normalize indirectly via
    # configs -> matcher, so resolving it at module load creates a cycle.
    from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
    attr = PatientInfoAttributes(pi)
    pi.mipi_risk = attr.mipi_risk
    pi.mipi_c_risk = attr.mipi_c_risk
    # bulky_disease_criteria and high_risk_mcl_criteria are comma-joined
    # strings (or None), per CB.
    pi.bulky_disease_criteria = attr.bulky_disease_criteria
    pi.high_risk_mcl_criteria = attr.high_risk_mcl_criteria
