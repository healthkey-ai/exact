import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.trial_match_explainer import TrialMatchExplainer
from tests.factories import TrialFactory


class TestTrialMatchExplainer:
    @pytest.mark.django_db
    def test_matched_attr(self):
        """Attr with no trial restriction → matched, patient value surfaced."""
        trial = TrialFactory(age_low_limit=None, age_high_limit=None)
        patient_info = PatientInfo(disease='multiple myeloma', patient_age=45)

        reasons = TrialMatchExplainer(trial, patient_info).explain()

        age_reason = next((r for r in reasons if r['attr'] == 'patient_age'), None)
        assert age_reason is not None
        assert age_reason['status'] == 'matched'
        assert age_reason['patientValue'] == 45

    @pytest.mark.django_db
    def test_unknown_attr(self):
        """Attr with trial restriction but blank patient value → unknown."""
        trial = TrialFactory(age_low_limit=18, age_high_limit=75)
        patient_info = PatientInfo(disease='multiple myeloma', patient_age=None)

        reasons = TrialMatchExplainer(trial, patient_info).explain()

        age_reason = next((r for r in reasons if r['attr'] == 'patient_age'), None)
        assert age_reason is not None
        assert age_reason['status'] == 'unknown'
        assert age_reason['patientValue'] is None

    @pytest.mark.django_db
    def test_not_matched_attr(self):
        """Patient value outside trial range → not_matched."""
        trial = TrialFactory(age_low_limit=18, age_high_limit=65)
        patient_info = PatientInfo(disease='multiple myeloma', patient_age=70)

        reasons = TrialMatchExplainer(trial, patient_info).explain()

        age_reason = next((r for r in reasons if r['attr'] == 'patient_age'), None)
        assert age_reason is not None
        assert age_reason['status'] == 'not_matched'

    @pytest.mark.django_db
    def test_disease_filtered_attr_excluded(self):
        """FL-only attrs (flipi_score_options, tumor_grade) are excluded for MM trials."""
        trial = TrialFactory(disease='Multiple Myeloma')
        patient_info = PatientInfo(disease='multiple myeloma')

        reasons = TrialMatchExplainer(trial, patient_info).explain()
        attr_names = [r['attr'] for r in reasons]

        assert 'flipi_score_options' not in attr_names
        assert 'tumor_grade' not in attr_names

    @pytest.mark.django_db
    def test_disease_filtered_attr_included_for_fl(self):
        """FL-only attrs are included when trial disease is Follicular Lymphoma."""
        trial = TrialFactory(disease='Follicular Lymphoma')
        patient_info = PatientInfo(disease='follicular lymphoma')

        reasons = TrialMatchExplainer(trial, patient_info).explain()
        attr_names = [r['attr'] for r in reasons]

        assert 'flipi_score_options' in attr_names
        assert 'tumor_grade' in attr_names

    @pytest.mark.django_db
    def test_sort_order(self):
        """not_matched entries appear before unknown, which appear before matched."""
        # age disqualifies (not_matched); ecog is blank (unknown); rest matched
        trial = TrialFactory(
            age_low_limit=18,
            age_high_limit=60,
            ecog_performance_status_max=2,
        )
        patient_info = PatientInfo(
            disease='multiple myeloma',
            patient_age=70,       # not_matched
            ecog_performance_status=None,  # unknown
        )

        reasons = TrialMatchExplainer(trial, patient_info).explain()
        statuses = [r['status'] for r in reasons]

        # All not_matched come before any unknown; all unknown before any matched
        last_not_matched = max((i for i, s in enumerate(statuses) if s == 'not_matched'), default=-1)
        first_unknown = next((i for i, s in enumerate(statuses) if s == 'unknown'), len(statuses))
        first_matched = next((i for i, s in enumerate(statuses) if s == 'matched'), len(statuses))

        assert last_not_matched < first_unknown
        assert first_unknown <= first_matched

    @pytest.mark.django_db
    def test_result_shape(self):
        """Each entry has the four expected keys."""
        trial = TrialFactory()
        patient_info = PatientInfo(disease='multiple myeloma')

        reasons = TrialMatchExplainer(trial, patient_info).explain()

        assert len(reasons) > 0
        for reason in reasons:
            assert set(reason.keys()) == {'attr', 'status', 'patientValue', 'trialRequirement'}
            assert reason['status'] in {'matched', 'unknown', 'not_matched'}
