"""#4840 port: lab sane_range guards must keep the SQL filter and the matcher in
agreement for a patient who actually carries a value against an out-of-band
(bi-scaled) trial threshold. If sane_range only reaches one side, they diverge.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.matching import status_equivalence as se
from tests.factories import TrialFactory


@pytest.mark.django_db
def test_hemoglobin_out_of_band_threshold_no_sql_matcher_divergence():
    # Patient 9.0 g/dL; trial requires hemoglobin_min=95 (a g/L magnitude wearing
    # the g/dL label — outside the (2,25) band). sane_range should make BOTH the
    # SQL filter and the matcher ignore the threshold, so no divergence.
    trial = TrialFactory(disease='multiple myeloma', hemoglobin_level_min=95)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65,
                     hemoglobin_level=9.0, hemoglobin_level_units='g/dL')
    base = Trial.objects.filter(id=trial.id)

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    sql = se.sql_status_map(base, pi)
    print(f"\nmatcher={verdict} sql={sql[trial.id]}")
    assert se.compare(base, pi) == [], (
        f"out-of-band hemoglobin threshold diverges: matcher={verdict} sql={sql[trial.id]}"
    )


@pytest.mark.django_db
def test_creatinine_decimal_band_out_of_band_no_divergence():
    # Exercises the Decimal sane_range band (0.1, 25): patient 1.0 mg/dL vs an
    # out-of-band creatinine_abs_min of 88 (a µmol/L magnitude on the mg/dL col).
    trial = TrialFactory(disease='multiple myeloma', serum_creatinine_level_abs_min=88)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65,
                     serum_creatinine_level=1.0, serum_creatinine_level_units='mg/dL')
    base = Trial.objects.filter(id=trial.id)
    divs = se.compare(base, pi)
    assert divs == [], f"creatinine Decimal-band diverges: {[(d.direction, d.matcher_not_matched_attrs) for d in divs]}"


@pytest.mark.django_db
def test_albumin_out_of_band_threshold_no_divergence():
    # albumin gains a units_convertor + (1,7) g/dL guard. A g/L mislabel (25 on
    # the g/dL column) is out-of-band -> ignored on both sides, no divergence.
    trial = TrialFactory(disease='multiple myeloma', albumin_min=25)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65,
                     albumin_level=4.0, albumin_level_units='G/DL')
    base = Trial.objects.filter(id=trial.id)
    divs = se.compare(base, pi)
    assert divs == [], f"albumin out-of-band diverges: {[(d.direction, d.matcher_not_matched_attrs) for d in divs]}"


@pytest.mark.django_db
def test_albumin_g_per_l_input_is_converted():
    # The new units_convertor: a patient who entered G/L must be converted to
    # g/dL before comparison. Discriminating case — 45 G/L = 4.5 g/dL fails a
    # 5 g/dL minimum (not_eligible), whereas the raw 45 would wrongly clear it.
    # (5 is in-band, so sane_range keeps it a real constraint.)
    trial = TrialFactory(disease='multiple myeloma', albumin_min=5)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65,
                     albumin_level=45, albumin_level_units='G/L')

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'not_eligible', (
        f"45 G/L (=4.5 g/dL) vs a 5 g/dL min should be not_eligible; "
        f"got {verdict} (raw 45 would wrongly match — convertor not applied?)"
    )


@pytest.mark.django_db
def test_in_band_hemoglobin_threshold_still_excludes():
    # Guard: an IN-band threshold the patient genuinely fails must still exclude
    # (sane_range only skips out-of-band thresholds, not real ones).
    trial = TrialFactory(disease='multiple myeloma', hemoglobin_level_min=12)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65,
                     hemoglobin_level=9.0, hemoglobin_level_units='g/dL')

    verdict = UserToTrialAttrMatcher(trial, pi).trial_match_status()
    assert verdict == 'not_eligible', f"9.0 g/dL vs a 12 g/dL min should be not_eligible, got {verdict}"
