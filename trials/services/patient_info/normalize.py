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
    if str(pi.disease).lower() != 'mantle cell lymphoma':
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
