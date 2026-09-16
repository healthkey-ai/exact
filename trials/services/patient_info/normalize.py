"""
Pure function to compute derived PatientInfo fields.

Called from resolve_patient_info() when constructing an in-memory instance.
"""
from django.contrib.gis.geos import Point

from trials.services.patient_info.patient_info_flipi_score import PatientInfoFlipyScore
from trials.services.patient_info.patient_info_geo_point import PatientInfoGeoPoint


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


def _unknown_or_false(pi, name):
    """None when the caller explicitly said null, else the legacy False.

    Imported lazily for the same reason `PatientInfoAttributes` is: this module
    is reached from the attribute service's own import graph.
    """
    from trials.services.patient_info.patient_info_attributes import explicitly_unknown

    return None if explicitly_unknown(pi, name) else False


# Triple negative means all three receptors negative. "Unknown" is spelled both
# as None and as '' in this vocabulary (value_options renders '' as "Unknown"),
# and neither is a negative receptor.
_TNBC_NEGATIVE = (
    ('estrogen_receptor_status', 'er_minus'),
    ('progesterone_receptor_status', 'pr_minus'),
    ('her2_status', 'her2_minus'),
)


def _normalize_tnbc_status(pi) -> None:
    stated = [(getattr(pi, field), negative) for field, negative in _TNBC_NEGATIVE]

    if all(value == negative for value, negative in stated):
        pi.tnbc_status = True
    elif any(value not in (None, '') and value != negative for value, negative in stated):
        # ONE stated positive settles it: the tumour is not triple negative,
        # whatever the other two turn out to be. Answering "unknown" here would
        # keep a TNBC-only trial as a candidate for a patient whose own ER+
        # rules it out.
        pi.tnbc_status = False
    else:
        # Nothing contradicts and something is missing. The single `else` this
        # replaces answered False for that too, so a caller saying
        # `tnbc_status: null` got a confirmed NO back.
        pi.tnbc_status = _unknown_or_false(pi, 'tnbc_status')


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
        # Determined, not missing: the attribute is breast-cancer scoped, so
        # False is an answer here and an explicit null does not change it.
        pi.metastatic_status = False
        return
    if pi.stage in (None, ''):
        # '' is how "Unknown" is spelled in this vocabulary, same as for the
        # receptors above. A blank stage is no evidence that the disease is not
        # metastatic.
        pi.metastatic_status = _unknown_or_false(pi, 'metastatic_status')
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
