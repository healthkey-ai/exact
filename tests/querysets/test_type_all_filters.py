"""`?type=all` honours the reader's filters (#424).

"All" means "do not narrow by how this patient matches". It has never meant
"ignore what I asked for" — but `filter_for_admin` applied six of the study
filters and silently dropped the rest, so a request carrying a sponsor and a
phase came back as a plausible list that honoured neither.

The last test here is the one that matters most: the two paths drifting apart
is HOW this happened, so it fails if a filter is added to one and not the
other.
"""
import inspect
import re
import datetime as dt

import pytest

from trials.models import Trial
from trials.querysets.trial import TrialQuerySet
from trials.services.study_preferences import StudyPreferences
from tests.factories import TrialFactory


def _search(**prefs):
    """The `?type=all` path, as `filtered_trials` reaches it."""
    study_info = StudyPreferences(**prefs)
    query, _ = Trial.objects.all().filtered_trials(
        search_options={}, study_info=study_info, patient_info=None, search_type='all',
    )
    return {t.study_id for t in query}


@pytest.mark.django_db
class TestTypeAllHonoursTheFilters:
    def test_sponsor(self):
        TrialFactory(study_id='KEEP', sponsor_name='Janssen')
        TrialFactory(study_id='DROP', sponsor_name='Someone else')

        assert _search(sponsor='Janssen') == {'KEEP'}

    def test_phase(self):
        # `phase_code_min`, not `phases`: the filter reads the denormalised
        # column, as `test_by_phase` does.
        TrialFactory(study_id='KEEP', phase_code_min=3)
        TrialFactory(study_id='DROP', phase_code_min=0)

        assert _search(phase='PHASE3') == {'KEEP'}

    def test_recruitment_status(self):
        TrialFactory(study_id='KEEP', recruitment_status='RECRUITING')
        TrialFactory(study_id='DROP', recruitment_status='COMPLETED')

        assert _search(recruitment_status='RECRUITING') == {'KEEP'}

    def test_last_update(self):
        TrialFactory(study_id='KEEP', last_update_date=dt.date.today())
        TrialFactory(study_id='DROP', last_update_date=dt.date.today() - dt.timedelta(days=365 * 4))

        assert _search(last_update=1) == {'KEEP'}

    def test_intervention_treatment(self):
        # The full-text column the filter actually searches.
        TrialFactory(study_id='KEEP', intervention_treatments_text='Daratumumab, Lenalidomide')
        TrialFactory(study_id='DROP', intervention_treatments_text='Something Else')

        assert _search(search_treatment='Daratumumab') == {'KEEP'}

    def test_first_enrolment(self):
        """The one newly applied filter with no behavioural test in the first
        version — and the guard matches method NAMES, so
        `by_first_enrolment_date(study_info.last_update)`, a plausible slip in
        a block of five near-identical lines, would have passed everything."""
        TrialFactory(study_id='KEEP', first_enrolment_date=dt.date.today())
        TrialFactory(study_id='DROP', first_enrolment_date=dt.date.today() - dt.timedelta(days=365 * 4))

        assert _search(first_enrolment=1) == {'KEEP'}

    def test_a_phase_filter_also_drops_the_trials_whose_phase_is_unknown(self):
        """`by_phase` compares `phase_code_min__gte`, which is false for NULL —
        and 13% of the real corpus has no phase recorded.

        Pinned because "all" is the tab where a silent drop is least expected,
        and because the before/after table in the commit reads as "not phase 3"
        when it also means "phase not recorded". Same behaviour as the standard
        path, so this records it rather than changes it."""
        TrialFactory(study_id='KEEP', phase_code_min=3)
        TrialFactory(study_id='UNKNOWN_PHASE', phase_code_min=None)

        assert _search(phase='PHASE3') == {'KEEP'}

    def test_the_filters_it_already_applied_still_apply(self):
        """A fix that turned the working half off would pass every test above."""
        TrialFactory(study_id='KEEP', brief_title='Daratumumab study')
        TrialFactory(study_id='DROP', brief_title='Something else')

        assert _search(search_title='Daratumumab') == {'KEEP'}

    def test_all_still_means_no_patient_narrowing(self):
        """The point of the branch: what it must NOT start doing."""
        TrialFactory(study_id='YOUNG_ONLY', age_low_limit=80)
        TrialFactory(study_id='OPEN')

        # No patient, no eligibility narrowing — both come back.
        assert _search() == {'YOUNG_ONLY', 'OPEN'}


class TestTheTwoPathsCannotDriftAgain:
    """`filter_for_admin` and `filter_by_study_info` are two hand-written lists
    of the same filters, and #424 is what happened when they diverged.

    Reading the source is a blunt instrument, but it catches the thing that
    actually goes wrong: someone adds `by_new_filter` to one and not the other,
    and nothing fails.
    """

    #: Applied by the standard path and deliberately NOT by the admin one.
    #: An entry here is a decision, and it needs a reason.
    DELIBERATELY_ADMIN_ONLY_SKIPS = {
        # `by_location` resolves a country by TITLE, and the catalog holds two
        # rows for the United States — one with 18,365 site links and one with
        # a single link. The UI seeds `country` from the patient's profile on
        # every request, so the second spelling silently narrows `?type=all`
        # to one trial. See #430 and the comment in `filtered_trials`.
        'location',
    }

    #: Applied by the ADMIN path and not by the standard one. `by_study_id` is
    #: here because that is the state of the code, not because it is right:
    #: `GET /trials/search/?studyId=NCT123` ignores the id and answers with the
    #: whole matched corpus — #424's own shape, in the other direction. Filed
    #: separately rather than fixed in passing, since it changes what the
    #: default tab returns and wants its own before/after.
    DELIBERATELY_STANDARD_ONLY_SKIPS = {'study_id'}

    @staticmethod
    def _filters_in(source: str) -> set:
        # Comment lines stripped first: commenting a call out is the likeliest
        # way someone disables a filter in a hurry, and reading raw source
        # would count the disabled line as applied. Prose that names a filter
        # would count too.
        code = '\n'.join(
            line for line in source.splitlines() if not line.lstrip().startswith('#')
        )
        return set(re.findall(r'\.by_(\w+)\(', code))

    @classmethod
    def _admin_filters(cls) -> set:
        # Only the admin BRANCH of `filtered_trials`, not the whole method: the
        # `else` branch belongs to the standard path, and reading it would
        # credit a filter added there to the admin path — the very drift this
        # guards against, passing green.
        source = inspect.getsource(TrialQuerySet.filtered_trials)
        prelude, rest = source.split("if search_type in ['all'")
        branch = rest.split('else:')[0]
        # The prelude counts too — `by_therapy_id` is applied there, before the
        # split, and belongs to both paths.
        return (
            cls._filters_in(prelude)
            | cls._filters_in(branch)
            | cls._filters_in(inspect.getsource(TrialQuerySet.filter_for_admin))
        )

    def test_the_admin_path_applies_every_filter_the_standard_one_does(self):
        standard = self._filters_in(inspect.getsource(TrialQuerySet.filter_by_study_info))

        missing = standard - self._admin_filters() - self.DELIBERATELY_ADMIN_ONLY_SKIPS
        assert not missing, (
            f'`?type=all` would silently ignore: {sorted(missing)}. '
            'Add them to `filter_for_admin`, or name them in '
            'DELIBERATELY_ADMIN_ONLY_SKIPS with a reason.'
        )

    def test_and_the_standard_path_applies_every_filter_the_admin_one_does(self):
        """The other direction, which the first version of this guard could not
        see — and something is already hiding there: `by_study_id`."""
        standard = self._filters_in(inspect.getsource(TrialQuerySet.filter_by_study_info))

        missing = self._admin_filters() - standard - self.DELIBERATELY_STANDARD_ONLY_SKIPS
        assert not missing, (
            f'an ordinary search would silently ignore: {sorted(missing)}. '
            'Add them to `filter_by_study_info`, or name them in '
            'DELIBERATELY_STANDARD_ONLY_SKIPS with a reason.'
        )


@pytest.mark.django_db
class TestTypeAllDoesNotNarrowByCountry:
    """The one filter from the standard path this deliberately does not copy.

    `by_location` resolves a country by TITLE, and the catalog holds two rows
    for the United States: `United States`, with 18,365 site links in the real
    corpus, and `United States of America`, with ONE. The federated UI seeds
    `country` from the patient's profile on every request, so a patient
    carrying the second spelling would watch `?type=all` narrow from the whole
    corpus to a single trial — plausible, non-empty, wrong, and exactly the
    failure this change exists to remove.

    An earlier version of this file asserted the opposite: that the filter was
    "inert", pinned with a code and a title that the UI never sends. It was
    green and it was wrong.
    """

    def _corpus(self):
        from trials.models import Country, Location, LocationTrial

        big = Country.objects.create(title='United States')
        stray = Country.objects.create(title='United States of America')
        for study_id, country in (('A', big), ('B', big), ('C', stray)):
            trial = TrialFactory(study_id=study_id)
            LocationTrial.objects.create(
                trial=trial,
                location=Location.objects.create(
                    city='X', title=f'Site {study_id}', country=country,
                ),
            )
        TrialFactory(study_id='NOWHERE')

    def test_the_spelling_that_would_truncate_does_not(self):
        self._corpus()
        assert _search(country='United States of America') == {'A', 'B', 'C', 'NOWHERE'}

    def test_nor_does_the_one_that_would_work(self):
        """Not narrowing at all is the decision, not "narrowing only sometimes"
        — a filter that works for one spelling of a country and truncates on
        another is worse than one that visibly does nothing."""
        self._corpus()
        assert _search(country='United States') == {'A', 'B', 'C', 'NOWHERE'}


@pytest.mark.django_db
class TestTypeAllHonoursTheRadius:
    """The distance branch, the other newly applied path with no test.

    It lives in `filtered_trials` rather than in `filter_for_admin` because
    `by_distance` reads the annotation built there — so an ordering mistake
    would show up here rather than as a wrong count somewhere else.
    """

    def _patient_at(self, latitude, longitude):
        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.normalize import normalize_patient_info

        pi = PatientInfo(disease='multiple myeloma', latitude=latitude, longitude=longitude)
        normalize_patient_info(pi)
        return pi

    def _trial_at(self, study_id, latitude, longitude):
        from trials.models import Location, LocationTrial
        from django.contrib.gis.geos import Point

        trial = TrialFactory(study_id=study_id, disease='multiple myeloma')
        LocationTrial.objects.create(
            trial=trial,
            location=Location.objects.create(
                city=study_id, title=f'Site {study_id}',
                geo_point=Point(longitude, latitude, srid=4326),
            ),
        )
        return trial

    def test_a_radius_narrows_to_what_is_inside_it(self):
        self._trial_at('NEAR', 40.75, -73.99)   # Manhattan
        self._trial_at('FAR', 37.77, -122.42)   # San Francisco

        patient = self._patient_at(40.71, -74.00)
        query, _ = Trial.objects.all().filtered_trials(
            search_options={},
            study_info=StudyPreferences(distance=50, distance_units='km'),
            patient_info=patient,
            search_type='all',
        )

        assert {t.study_id for t in query} == {'NEAR'}
