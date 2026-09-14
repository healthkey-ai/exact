"""`?studyId=` narrows an ordinary search, not only `?type=all` (#458).

`by_study_id` was applied in `filter_for_admin` and nowhere else, so a caller
naming one trial on the default path got every trial the patient matched.
Measured on the real corpus before the fix: 3114 rows against 1 for the same
id with `?type=all`.

Same shape as #424 — a parameter neither rejected nor honoured — in the
direction #424's own guard could not see, which is how it was found.
"""
import pytest

from trials.models import Trial
from trials.services.study_preferences import StudyPreferences
from tests.factories import TrialFactory


def _search(search_type=None, **prefs):
    query, _ = Trial.objects.all().filtered_trials(
        search_options={}, study_info=StudyPreferences(**prefs),
        patient_info=None, search_type=search_type,
    )
    return {t.study_id for t in query}


@pytest.mark.django_db
class TestStudyIdNarrowsAnOrdinarySearch:
    def test_it_narrows_to_the_trial_that_was_named(self):
        TrialFactory(study_id='NCT_WANTED')
        TrialFactory(study_id='NCT_OTHER')

        assert _search(study_id='NCT_WANTED') == {'NCT_WANTED'}

    def test_and_still_does_on_the_all_branch(self):
        """The half that already worked — a fix that moved the filter rather
        than adding it would pass the test above and break this one."""
        TrialFactory(study_id='NCT_WANTED')
        TrialFactory(study_id='NCT_OTHER')

        assert _search(search_type='all', study_id='NCT_WANTED') == {'NCT_WANTED'}

    def test_an_id_nobody_has_returns_nothing_rather_than_everything(self):
        """The failure this fixes, stated the other way round: the old
        behaviour for an unknown id was the whole corpus."""
        TrialFactory(study_id='NCT_WANTED')
        TrialFactory(study_id='NCT_OTHER')

        assert _search(study_id='NCT_NOT_HERE') == set()

    def test_no_id_still_means_no_narrowing(self):
        TrialFactory(study_id='A')
        TrialFactory(study_id='B')

        assert _search() == {'A', 'B'}
        assert _search(study_id='') == {'A', 'B'}
        assert _search(study_id=None) == {'A', 'B'}

    def test_it_narrows_alongside_the_other_filters_rather_than_instead_of_them(self):
        """Applied in the same block as the rest, so a request carrying both
        gets both — and an id that does not match the other filters returns
        nothing, which is the honest answer to a contradictory request."""
        TrialFactory(study_id='NCT_WANTED', sponsor_name='Janssen')
        TrialFactory(study_id='NCT_OTHER', sponsor_name='Janssen')

        assert _search(study_id='NCT_WANTED', sponsor='Janssen') == {'NCT_WANTED'}
        assert _search(study_id='NCT_WANTED', sponsor='Somebody else') == set()

    def test_it_forgives_the_case_and_the_stray_space(self):
        """Strictness was cheap while this was `?type=all`-only: a miss meant
        the whole corpus came back, which is obviously wrong. On the ordinary
        path the same miss returns NOTHING, and "no trials" reads as "that
        trial does not exist" rather than "you typed it in lower case".

        Every sibling text filter here is already lenient."""
        TrialFactory(study_id='NCT01145989')

        assert _search(study_id='nct01145989') == {'NCT01145989'}
        assert _search(study_id=' NCT01145989 ') == {'NCT01145989'}
        assert _search(study_id='   ') == {'NCT01145989'}  # blank after strip: no filter


@pytest.mark.django_db
class TestTheChosenSemanticIsPinned:
    """`by_study_id` runs BEFORE the patient filter, so a named trial the
    patient does not match returns an empty list rather than the trial.

    None of the other tests exercise that — they all pass `patient_info=None` —
    so a later change that special-cased `study_id` to bypass the patient
    filter (the plausible response to a "why is my trial missing?" report)
    would keep every one of them green.
    """

    def test_a_named_trial_the_patient_cannot_enter_is_not_returned(self):
        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.normalize import normalize_patient_info

        TrialFactory(study_id='NCT_TOO_OLD', disease='multiple myeloma', age_low_limit=80)
        patient = PatientInfo(disease='multiple myeloma', patient_age=45)
        normalize_patient_info(patient)

        query, _ = Trial.objects.all().filtered_trials(
            search_options={}, study_info=StudyPreferences(study_id='NCT_TOO_OLD'),
            patient_info=patient, search_type=None,
        )

        assert list(query) == []

    def test_and_type_all_is_the_way_to_get_it_anyway(self):
        """The escape hatch the comment points at — NOT `GET /trials/{id}/`,
        which is keyed on the internal primary key, so a caller holding an NCT
        number cannot reach it without a search."""
        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.normalize import normalize_patient_info

        TrialFactory(study_id='NCT_TOO_OLD', disease='multiple myeloma', age_low_limit=80)
        patient = PatientInfo(disease='multiple myeloma', patient_age=45)
        normalize_patient_info(patient)

        query, _ = Trial.objects.all().filtered_trials(
            search_options={}, study_info=StudyPreferences(study_id='NCT_TOO_OLD'),
            patient_info=patient, search_type='all',
        )

        assert {t.study_id for t in query} == {'NCT_TOO_OLD'}
