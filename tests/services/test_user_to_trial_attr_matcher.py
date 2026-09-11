import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory


class TestUserToTrialAttrMatcher:
    @pytest.mark.parametrize('disease, expected_code', [
        ('multiple myeloma', 'MM'),
        ('follicular lymphoma', 'FL'),
        ('breast cancer', 'BC'),
        ('chronic lymphocytic leukemia', 'CLL'),
        ('mantle cell lymphoma', 'MCL'),
        ('Mantle Cell Lymphoma', 'MCL'),  # casing tolerance
        ('something else', None),
    ])
    @pytest.mark.django_db
    def test_disease_code_normalization(self, disease, expected_code):
        trial = TrialFactory(disease=disease)
        patient_info = PatientInfo(disease=disease)

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.disease_code == expected_code

    @pytest.mark.django_db
    def test_potential_attrs_to_check(self):
        trial = TrialFactory()
        patient_info = PatientInfo(disease='multiple myeloma')

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.trial_match_status() == 'eligible'

        trial.age_low_limit = 18
        trial.save()
        assert service.trial_match_status() == 'potential'

        patient_info.patient_age = 16
        assert service.trial_match_status() == 'not_eligible'

    @pytest.mark.django_db
    def test_therapy_related_things_mismatch_status(self):
        trial = TrialFactory()
        patient_info = PatientInfo(disease='multiple myeloma')

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.therapy_related_things_mismatch_status() == 'unknown'

        patient_info.prior_therapy = 'None'
        assert service.therapy_related_things_mismatch_status() == 'not_matched'

        patient_info.prior_therapy = 'More than two lines of therapy'
        assert service.therapy_related_things_mismatch_status() == 'unknown'

    @pytest.mark.django_db
    def test_therapy_related_things_match_status(self):
        patient_info = PatientInfo(disease='multiple myeloma', prior_therapy='None')

        trial1 = TrialFactory(therapies_required=['vrd'])
        trial2 = TrialFactory(therapies_excluded=['vrd'])

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'not_matched', 'values': []},
            'therapiesExcluded': {'status': 'matched', 'values': []},
            'therapyTypesRequired': {'status': 'matched', 'values': []},
            'therapyTypesExcluded': {'status': 'matched', 'values': []},
            'therapyComponentsRequired': {'status': 'matched', 'values': []},
            'therapyComponentsExcluded': {'status': 'matched', 'values': []}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': []},
            'therapiesExcluded': {'status': 'matched', 'values': []},
            'therapyTypesRequired': {'status': 'matched', 'values': []},
            'therapyTypesExcluded': {'status': 'matched', 'values': []},
            'therapyComponentsRequired': {'status': 'matched', 'values': []},
            'therapyComponentsExcluded': {'status': 'matched', 'values': []}
        }

        patient_info.prior_therapy = 'One line'
        patient_info.first_line_therapy = 'dara_vrd'

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'not_matched', 'values': ['Dara-VRd']},
            'therapiesExcluded': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapiesExcluded': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']}
        }

        patient_info.first_line_therapy = 'vrd'

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['**VRd**']},
            'therapiesExcluded': {'status': 'matched', 'values': ['VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['VRd']},
            'therapiesExcluded': {'status': 'not_matched', 'values': ['**VRd**']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']}
        }

    @pytest.mark.django_db
    def test_therapy_related_things_match_status_does_not_n_plus_1(self):
        """The component/category load must not scale with the patient's
        therapy count. `therapy.components.order_by('id')` (no prefetch) once
        fired a query per therapy + per component — an N+1 in this per-request,
        per-trial matcher hot path. The fix prefetches `components__categories`
        and sorts in Python, so the query count is constant regardless of how
        many therapies the patient carries.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from trials.models import Therapy

        codes_with_components = [
            t.code for t in Therapy.objects.prefetch_related('components').all()
            if t.components.all()
        ]
        assert len(codes_with_components) >= 4, 'seed data needs therapies with components'

        trial = TrialFactory(therapies_required=['vrd'])

        def queries_for(n):
            pi = PatientInfo(disease='multiple myeloma')
            pi.later_therapies = [{'therapy': c} for c in codes_with_components[:n]]
            service = UserToTrialAttrMatcher(trial, pi)
            with CaptureQueriesContext(connection) as ctx:
                service.therapy_related_things_match_status()
            return len(ctx.captured_queries)

        few = queries_for(2)
        many = queries_for(len(codes_with_components))
        # Constant query count proves no per-therapy N+1.
        assert few == many, f'query count scales with therapy count ({few} -> {many}); N+1 regressed'
        assert many <= 5, f'expected a small constant query count, got {many}'

    @pytest.mark.django_db
    def test_receptor_status_hierarchy(self):
        """
        er_plus_with_hi_exp / er_plus_with_low_exp are subtypes of er_plus.
        The matcher must return 'eligible' for a BC trial requiring er_plus when
        the patient has one of those subtypes, and 'not_eligible' for er_minus.
        Same logic applies to PR (pr_plus) and HR (hr_plus).
        """
        # --- ER ---
        trial_er = TrialFactory(disease='breast cancer', estrogen_receptor_statuses_required=['er_plus'])

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_plus_with_low_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_minus'
        )).trial_match_status() == 'not_eligible'

        # --- PR ---
        trial_pr = TrialFactory(disease='breast cancer', progesterone_receptor_statuses_required=['pr_plus'])

        assert UserToTrialAttrMatcher(trial_pr, PatientInfo(
            disease='breast cancer', progesterone_receptor_status='pr_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_pr, PatientInfo(
            disease='breast cancer', progesterone_receptor_status='pr_minus'
        )).trial_match_status() == 'not_eligible'

        # --- HR ---
        trial_hr = TrialFactory(disease='breast cancer', hr_statuses_required=['hr_plus'])

        assert UserToTrialAttrMatcher(trial_hr, PatientInfo(
            disease='breast cancer', hr_status='hr_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_hr, PatientInfo(
            disease='breast cancer', hr_status='hr_minus'
        )).trial_match_status() == 'not_eligible'

    @pytest.mark.django_db
    def test_treatment_refractory_status_unknown_when_falsy(self):
        """
        treatment_refractory_status should return 'unknown' for any falsy patient value
        (None, empty string), not only for None.  Matches CB's `if not value` logic.
        """
        # Trial that requires NOT refractory
        trial = TrialFactory(not_refractory_required=True)
        pi = PatientInfo(disease='multiple myeloma')

        matcher = UserToTrialAttrMatcher(trial, pi)

        # None → unknown
        pi.treatment_refractory_status = None
        assert matcher.attr_match_status('treatment_refractory_status') == 'unknown'

        # Empty string → unknown (this was the bug: `is None` missed '')
        pi.treatment_refractory_status = ''
        assert matcher.attr_match_status('treatment_refractory_status') == 'unknown'

        # A refractory patient → not_matched (the trial wants not-refractory)
        pi.treatment_refractory_status = 'primaryRefractory'
        assert matcher.attr_match_status('treatment_refractory_status') == 'not_matched'

        # A not-refractory patient → matched
        pi.treatment_refractory_status = 'notRefractory'
        assert matcher.attr_match_status('treatment_refractory_status') == 'matched'

    @pytest.mark.django_db
    def test_treatment_refractory_status_trial_has_no_requirement(self):
        """Neither refractory status required → not_evaluated, whatever the
        patient's status is. The trial asked nothing here, so there is no
        verdict to give — and reporting one put the attribute into the match
        score for every patient equally."""
        trial = TrialFactory(not_refractory_required=False, refractory_required=False)
        pi = PatientInfo(disease='multiple myeloma')
        matcher = UserToTrialAttrMatcher(trial, pi)

        for value in (None, '', 'notRefractory', 'primaryRefractory'):
            pi.treatment_refractory_status = value
            assert matcher.attr_match_status('treatment_refractory_status') == 'not_evaluated', \
                f'Expected not_evaluated for value={value!r}'

    @pytest.mark.django_db
    def test_progression_empty_string_is_unknown(self):
        """Regression for #52 / CB #4306: the UI represents "Unknown"
        progression as '' (see ValueOptions.progressions). The matcher must
        treat '' the same as None, returning 'unknown' instead of falling
        through to 'not_matched' against a trial requiring active disease.
        """
        trial = TrialFactory(disease_progression_active_required=True)
        pi = PatientInfo(disease='multiple myeloma')
        matcher = UserToTrialAttrMatcher(trial, pi)

        # None → unknown (control, unchanged behavior)
        pi.progression = None
        assert matcher.attr_match_status('progression') == 'unknown'

        # Empty string → unknown (the fix path)
        pi.progression = ''
        assert matcher.attr_match_status('progression') == 'unknown'

        # 'active' patient → matched (sanity check)
        pi.progression = 'active'
        assert matcher.attr_match_status('progression') == 'matched'

        # 'smoldering' patient against active-required trial → not_matched
        pi.progression = 'smoldering'
        assert matcher.attr_match_status('progression') == 'not_matched'

    @pytest.mark.django_db
    def test_measurable_disease_imwg_unknown_for_empty_labs(self):
        """Regression for #54 / CB #4143-#4156: a patient with no IMWG-relevant
        lab values must show as Potential ('unknown'), not Ineligible
        ('not_matched'), against a trial requiring measurable_disease_imwg.

        Even when labs ARE present but the patient doesn't qualify (real False),
        the matcher should still return 'unknown' because measurable_disease_imwg
        is a derived/computed field the user cannot directly answer — surfacing
        the trial as Potential is the desired UX. The under_user_control flag
        in configs.py is what enforces this.
        """
        # Patient with all IMWG lab inputs blank → measurable_disease_imwg=None.
        pi = PatientInfo(
            disease='multiple myeloma',
            monoclonal_protein_serum=None,
            monoclonal_protein_urine=None,
            kappa_flc=None,
            lambda_flc=None,
        )
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is None

        # Trial doesn't require → matched regardless of patient value.
        # The trial does not require it, so nothing was checked.
        trial_no_req = TrialFactory(measurable_disease_imwg_required=None)
        assert UserToTrialAttrMatcher(trial_no_req, pi).attr_match_status('measurable_disease_imwg') == 'not_evaluated'

        # Trial requires + empty labs → 'unknown' (NOT 'not_matched').
        trial_requires = TrialFactory(measurable_disease_imwg_required=True)
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'unknown'

        # Patient has qualifying labs (serum M-protein >= 0.5) → 'matched'.
        pi.monoclonal_protein_serum = 5
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is True
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'matched'

        # Patient has labs but doesn't qualify (real False) → still 'unknown'
        # because under_user_control prefers Potential over hard-reject on a
        # derived value the user couldn't have answered themselves.
        pi.monoclonal_protein_serum = 0.1  # below 0.5 g/dL threshold
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is False
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'unknown'

    @pytest.mark.django_db
    def test_measurable_disease_imwg_zero_lab_is_real_false(self):
        """Regression for #81: serum / urine M-protein / FLC = 0 are real
        clinical measurements, not missing data. The IMWG normalizer
        previously used `if not pi.X` on serum and urine, and
        `if not ratio` on the kappa/lambda ratio — all three collapsed
        0 to None or False, misclassifying patients with real zero values.
        """
        # Patient with serum=0 + urine=0 + no FLC → serum and urine
        # components produce real False (was None pre-fix), FLC stays None.
        # Outer function: components=[False, False, None] → not-all-None →
        # derived value is False, not None.
        pi = PatientInfo(
            disease='multiple myeloma',
            monoclonal_protein_serum=0,
            monoclonal_protein_urine=0,
            kappa_flc=None,
            lambda_flc=None,
        )
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is False

        # End-to-end: matcher must still bucket this as 'unknown' against
        # a requiring trial because measurable_disease_imwg is under user
        # control (#54 contract preserved by this PR).
        trial_requires = TrialFactory(measurable_disease_imwg_required=True)
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'unknown'

        # Patient with serum=0 + qualifying urine → True (urine wins).
        pi.monoclonal_protein_urine = 300  # >= 200 threshold
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is True

        # Kappa FLC = 0, lambda FLC = 200: ratio = 0.0, well below the
        # 0.26 abnormal threshold. Pre-fix `if not ratio` returned False
        # here; post-fix the ratio is treated as the real abnormal value
        # it is. Lambda >= 100 makes kappa_lambda_abnormal_and_high True,
        # so derived IMWG flips to True.
        pi.monoclonal_protein_serum = None
        pi.monoclonal_protein_urine = None
        pi.kappa_flc = 0
        pi.lambda_flc = 200
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is True

        # Sanity: all-None inputs (true missing data) still derive None
        # (the #54 contract — Potential bucket).
        pi.kappa_flc = None
        pi.lambda_flc = None
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is None

    @pytest.mark.django_db
    def test_meets_slim_unknown_for_empty_labs(self):
        """Regression for #54 / CB #4143-#4156: same UX rule for meets_slim.

        The derivation (PatientInfoAttributes.meets_slim) already returns None
        for all-blank inputs, but without under_user_control in the config the
        matcher coerced None -> False and returned 'not_matched'. The fix is
        the under_user_control flag added when the meets_slim entry was
        uncommented in configs.py.
        """
        pi = PatientInfo(
            disease='multiple myeloma',
            clonal_plasma_cells=None,
            kappa_flc=None,
            lambda_flc=None,
            bone_lesions='',
        )
        normalize_patient_info(pi)
        assert pi.meets_slim is None

        # Trial doesn't require → matched.
        # The trial does not require it, so nothing was checked.
        trial_no_req = TrialFactory(meets_slim=None)
        assert UserToTrialAttrMatcher(trial_no_req, pi).attr_match_status('meets_slim') == 'not_evaluated'

        # Trial requires + empty labs → 'unknown' (NOT 'not_matched').
        trial_requires = TrialFactory(meets_slim=True)
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('meets_slim') == 'unknown'

        # Patient meets SLiM via the S component (>=60% clonal plasma cells).
        pi.clonal_plasma_cells = 65
        normalize_patient_info(pi)
        assert pi.meets_slim is True
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('meets_slim') == 'matched'

    @pytest.mark.django_db
    def test_peripheral_neuropathy_grade_zero_matches(self):
        """Regression for #53 / CB #4307: Grade 0 is a real clinical value,
        not blank. The configs.py entry must carry allow_blank_values=True
        so is_attr_blank() does not coerce 0 to 'unknown' against trials
        with peripheral_neuropathy_grade_max=0.
        """
        trial_no_limit = TrialFactory(disease='multiple myeloma')
        trial_max_zero = TrialFactory(disease='multiple myeloma', peripheral_neuropathy_grade_max=0)
        trial_max_two = TrialFactory(disease='multiple myeloma', peripheral_neuropathy_grade_max=2)

        pi = PatientInfo(disease='multiple myeloma')

        # Blank patient: the no-limit trial never asked (not_evaluated —
        # there is no requirement to have met); the max=0 trial asked and the
        # patient's data is missing, which is what `unknown` means.
        pi.peripheral_neuropathy_grade = None
        assert UserToTrialAttrMatcher(trial_no_limit, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_evaluated'
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'unknown'

        # Grade 0 patient: passes every trial that asks (this is the fix
        # path — used to be 'unknown' against max=0 because 0 was treated as
        # blank). The no-limit trial still asks nothing, so it reports
        # not_evaluated whatever the patient's grade is.
        pi.peripheral_neuropathy_grade = 0
        assert UserToTrialAttrMatcher(trial_no_limit, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_evaluated'
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'
        assert UserToTrialAttrMatcher(trial_max_two, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'

        # Grade 3 patient: excluded by any max< 3 trial.
        pi.peripheral_neuropathy_grade = 3
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_matched'
        assert UserToTrialAttrMatcher(trial_max_two, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_matched'


class TestNotEvaluatedKeepsTheScoreHonest:
    """An attribute the trial never constrained is not a match.

    It used to be reported as `matched`, which put it in the numerator AND
    the denominator of `trial_match_score` — so a trial that constrains two
    attributes out of forty scored near 100%, and the Matching sort ranked
    the least specific trials first.
    """

    @pytest.mark.django_db
    def test_an_unconstrained_attr_reports_not_evaluated(self):
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=None, age_high_limit=None)
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        m = UserToTrialAttrMatcher(trial, pi)
        assert m.attr_match_status('patient_age') == 'not_evaluated'

    @pytest.mark.django_db
    def test_a_constrained_attr_the_patient_meets_still_matches(self):
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=18, age_high_limit=75)
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        m = UserToTrialAttrMatcher(trial, pi)
        assert m.attr_match_status('patient_age') == 'matched'

    @pytest.mark.django_db
    def test_unconstrained_attrs_do_not_lift_the_score(self):
        """The same patient against a trial that asks one question and one
        that asks the same question plus nothing else: the score must not
        reward the second for the attributes it left empty."""
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=18, age_high_limit=75)
        score = UserToTrialAttrMatcher(trial, pi).trial_match_score()
        # Every other attribute on the factory-built trial is unconstrained,
        # so the only question asked was age — and the patient met it.
        assert score == 100

    @pytest.mark.django_db
    def test_a_trial_that_asks_almost_nothing_scores_on_what_it_asks(self):
        """The sweep's headline claim, measured.

        Against a factory-built trial that constrains only its disease, all
        133 mapped attributes but that one report `not_evaluated` — so the
        score is computed over the single question asked, not diluted by 132
        that were not. Before the sweep 92 of them claimed a match.
        """
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        trial = TrialFactory(disease='multiple myeloma')
        m = UserToTrialAttrMatcher(trial, pi)
        statuses = [m.attr_match_status(a) for a in m.mapping]
        evaluated = [s for s in statuses if s != 'not_evaluated']
        assert evaluated == ['matched'], (
            f'expected only the disease criterion to be evaluated, got {evaluated}'
        )
        assert m.trial_match_score() == 100

    @pytest.mark.django_db
    def test_a_trial_that_asks_nothing_at_all_matches_everybody(self):
        """0 and 100 are both reachable, and they must not be confused.

        With every attribute unevaluated the fraction has no denominator. The
        answer is 100 — a trial that constrains nothing rules nobody out —
        where 0 is the sentinel for a disqualification, and would have shown
        an eligible trial as a 0% match on the detail page.
        """
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        m = UserToTrialAttrMatcher(TrialFactory(disease=None), pi)
        assert [s for s in (m.attr_match_status(a) for a in m.mapping) if s != 'not_evaluated'] == []
        assert m.trial_match_score() == 100
        assert m.match_score_and_status() == (100, 'eligible')

    @pytest.mark.django_db
    def test_not_evaluated_does_not_change_eligibility(self):
        """It is neutral: it cannot disqualify, and it is not a data gap."""
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=None, age_high_limit=None)
        assert UserToTrialAttrMatcher(trial, pi).trial_match_status() == 'eligible'

    @pytest.mark.django_db
    def test_the_list_score_agrees_with_the_detail_score(self):
        """The card and the page it links to must not contradict each other.

        The list `match_score` is computed in SQL by `UserToTrialAttrsMapper`,
        the detail score in Python by this matcher. A `*_required` flag set to
        False is the case that pulled them apart: the matcher reads it as no
        constraint, while the SQL counted any non-NULL flag as a requirement
        met — 75 on the card against 66 on the page.
        """
        pi = PatientInfo(disease='multiple myeloma', patient_age=45, no_other_active_malignancies=True)
        normalize_patient_info(pi)
        base = dict(disease='multiple myeloma', age_low_limit=18, age_high_limit=75, toxicity_grade_max=2)
        cases = [({} if flag is None else {'no_other_active_malignancies_required': flag}, base)
                 for flag in (None, False, True)]
        # And the trial that constrains nothing at all, where the fraction has
        # no denominator: the matcher says 100, and a NULL annotation would
        # render as a blank card beside it.
        cases.append(({'disease': None}, {}))
        for kwargs, extra in cases:
            trial = TrialFactory(**extra, **kwargs)
            sql = (
                Trial.objects.filter(id=trial.id)
                .with_potential_attrs_count(pi)
                .values_list('match_score', flat=True)
                .first()
            )
            assert sql == UserToTrialAttrMatcher(trial, pi).trial_match_score(), (
                f'list and detail disagree for {kwargs or "an unconstrained trial"}'
            )

    @pytest.mark.django_db
    def test_the_single_pass_helper_agrees_with_the_two_methods(self):
        pi = PatientInfo(disease='multiple myeloma', patient_age=45)
        for kwargs in (
            {'age_low_limit': 18, 'age_high_limit': 75},
            {'age_low_limit': None, 'age_high_limit': None},
            {'age_low_limit': 60, 'age_high_limit': 75},
        ):
            m = UserToTrialAttrMatcher(TrialFactory(disease='multiple myeloma', **kwargs), pi)
            assert m.match_score_and_status() == (m.trial_match_score(), m.trial_match_status())
