"""A country pgeocode has no postal data for is not a 500 (#391).

`PatientInfoGeoPoint.point_by_country_and_postal_code` called
`pgeocode.Nominatim(country_code)` unguarded. pgeocode raises `ValueError` for
every country it does not ship data for, and this function sits on the
stateless patient path:

    resolve_patient_info -> _build_in_memory -> normalize_patient_info
                         -> _normalize_geo_point -> here

so any request whose patient carried a country and a postal code in the gap
answered 500 — on the list, the detail endpoint, the graph, anything that
resolves a patient. EXACT offers 249 countries as valid input; pgeocode covers
95. It was found when it killed a comparator sweep mid-run on an Egyptian
patient, which is the same code path a request takes.

"No postal data for this country" is the same answer as "no postal data for
this code", and the function already had one: None. The patient is still
matched to trials; they just cannot be ranked by distance.
"""
import pytest

from trials.services.patient_info.patient_info_geo_point import PatientInfoGeoPoint
from trials.services.patient_info.resolve import _build_in_memory


UNSUPPORTED = ['Egypt', 'Ecuador', 'Panama']


class TestTheGapBetweenWhatIsOfferedAndWhatCanBeGeocoded:
    def test_it_is_a_gap_worth_guarding(self):
        """Non-vacuity, and the measurement the fix is sized against: this is
        not one unlucky country. If pgeocode ever covers everything EXACT
        offers, this whole file becomes theatre and should say so."""
        import pgeocode

        from trials.services.patient_info.patient_info_geo_point import COUNTRIES_LIST

        supported = set(getattr(pgeocode, 'COUNTRIES_VALID', ()))
        assert supported, 'pgeocode no longer exposes its country list'
        assert len(set(COUNTRIES_LIST) - supported) > 100

    @pytest.mark.parametrize('country', UNSUPPORTED)
    def test_an_unsupported_country_geocodes_to_nothing(self, country):
        assert PatientInfoGeoPoint.point_by_country_and_postal_code(country, '11511') is None

    @pytest.mark.parametrize('country', UNSUPPORTED)
    def test_and_these_really_are_unsupported(self, country):
        """The assertion above passes for the wrong reason if pgeocode gains
        one of these but not the postal code used — `None` either way. Pinned
        against the support list so the fixture cannot quietly stop testing
        what it says it tests."""
        import pgeocode

        code = PatientInfoGeoPoint.country_code_by_country_code_or_name(country)
        assert code, f'{country} no longer resolves to a country code at all'
        assert code not in pgeocode.COUNTRIES_VALID

    def test_a_supported_country_still_geocodes(self):
        """The half that must not be lost: a guard wide enough to swallow the
        ValueError could swallow the answer too.

        Note this one reaches pgeocode's data for the US, which it DOWNLOADS on
        first use — the first run on a cold cache took ten minutes here, and on
        a machine with no network it would fail rather than skip. That is not
        introduced by this test: any existing test that builds a US patient
        with a postal code already pays it. Written down because a slow or
        offline CI failure here looks like a bug in the fix and is not one.
        """
        point = PatientInfoGeoPoint.point_by_country_and_postal_code('US', '02115')
        assert point is not None
        assert round(point.y) == 42 and round(point.x) == -71

    def test_an_unknown_postal_code_in_a_supported_country_is_also_nothing(self):
        """The pre-existing branch, asserted so the two paths cannot diverge:
        both mean "no point", and the caller treats them identically."""
        assert PatientInfoGeoPoint.point_by_country_and_postal_code('US', 'ZZZZZ') is None


@pytest.mark.django_db
class TestThePatientPathSurvivesIt:
    @pytest.mark.parametrize('country', UNSUPPORTED)
    def test_building_a_patient_does_not_raise(self, country):
        """The actual failure: not a missing geo point, but a ValueError
        escaping into every patient-context endpoint."""
        patient = _build_in_memory(
            {'disease': 'multiple myeloma', 'country': country, 'postal_code': '11511'}
        )
        assert patient.geo_point is None

    def test_and_the_postal_code_is_cleared_rather_than_left_dangling(self):
        """`_normalize_geo_point` clears it when no point resolves, so the
        patient does not carry a postal code that decides nothing."""
        patient = _build_in_memory(
            {'disease': 'multiple myeloma', 'country': 'Egypt', 'postal_code': '11511'}
        )
        assert not patient.postal_code

    def test_a_supported_country_still_gets_its_point(self):
        patient = _build_in_memory(
            {'disease': 'multiple myeloma', 'country': 'US', 'postal_code': '02115'}
        )
        assert patient.geo_point is not None


class TestTheUnexpectedFailureIsNotSilent:
    """The second clause, which had no coverage and — until review — could not
    fire for the case it was written for.

    `pandas.errors.EmptyDataError` and `ParserError` are ValueError
    SUBCLASSES, and pgeocode reads its cache with `pd.read_csv`. Catching
    `ValueError` to mean "unsupported country" therefore swallowed a corrupt
    cache for a SUPPORTED one and logged "no postal data for US" at debug. The
    support list is read up front now, so anything the call still raises is
    genuinely unexpected.
    """

    def _captured(self, call):
        import logging

        import trials.services.patient_info.patient_info_geo_point as module

        records = []
        handler = logging.Handler()
        handler.emit = records.append
        previous = module.logger.level
        module.logger.setLevel(logging.DEBUG)
        module.logger.addHandler(handler)
        try:
            result = call()
        finally:
            module.logger.removeHandler(handler)
            module.logger.setLevel(previous)
        return result, records

    def test_an_unsupported_country_is_not_logged_as_a_problem(self):
        """The other half of the split, and the reason the support list is
        read up front at all.

        Behaviourally the two branches are identical — both answer None — so
        only the log level tells them apart, and only this test holds them
        apart. A patient who lives somewhere pgeocode has no data for is an
        ordinary patient, not an incident, and warning on every such request
        would bury the one that matters.
        """
        import logging

        result, records = self._captured(
            lambda: PatientInfoGeoPoint.point_by_country_and_postal_code('Egypt', '11511')
        )
        assert result is None
        assert records, 'nothing was logged at all'
        assert all(r.levelno < logging.WARNING for r in records), (
            [r.getMessage() for r in records]
        )

    def test_a_supported_country_that_fails_anyway_warns_and_degrades(self, monkeypatch):
        import pandas

        import trials.services.patient_info.patient_info_geo_point as module

        def broken(*args, **kwargs):
            # The exact shape a truncated cache produces.
            raise pandas.errors.EmptyDataError('No columns to parse from file')

        monkeypatch.setattr(module.pgeocode, 'Nominatim', broken)
        with pytest.raises(Exception):
            broken()

        import logging

        result, records = self._captured(
            lambda: PatientInfoGeoPoint.point_by_country_and_postal_code('US', '02115')
        )

        assert result is None, 'a broken cache must not fail the request'
        assert any(r.levelno >= logging.WARNING for r in records), (
            'a supported country failing is not routine — it was logged at '
            'debug as "no postal data for US", which is false and invisible'
        )


class TestACountryThatIsNotAString:
    """Same defect class, same call chain, found by the same review.

    `country` comes straight off the inline payload — `_build_in_memory`
    coerces dates, numerics and JSON fields, not this — so a non-string
    reached `.upper()` and raised `AttributeError` out of
    `normalize_patient_info`.
    """

    @pytest.mark.parametrize('country', [123, ['Egypt'], {'name': 'Egypt'}, 12.5, True])
    def test_it_resolves_to_nothing_rather_than_raising(self, country):
        assert PatientInfoGeoPoint.country_code_by_country_code_or_name(country) is None
        assert PatientInfoGeoPoint.point_by_country_and_postal_code(country, '11511') is None

    @pytest.mark.django_db
    def test_and_the_patient_path_survives_it(self):
        patient = _build_in_memory(
            {'disease': 'multiple myeloma', 'country': 123, 'postal_code': '11511'}
        )
        assert patient.geo_point is None


class TestTheCountryAtIndexZero:
    """`if idx:` read index 0 as "not found", and index 0 is Afghanistan.

    `.index()` raises when the name is absent, so reaching that line already
    meant it was found — the truthiness test could only ever be wrong. The
    consequence was not a missing geo point: `_normalize_geo_point` clears
    `country` when no code resolves, so an Afghan patient lost their country
    field entirely.
    """

    def test_the_first_country_in_the_list_resolves(self):
        from trials.services.patient_info.patient_info_geo_point import COUNTRIES_LIST

        first_name = list(COUNTRIES_LIST.values())[0]
        first_code = list(COUNTRIES_LIST.keys())[0]
        assert PatientInfoGeoPoint.country_code_by_country_code_or_name(first_name) == first_code

    def test_by_name_since_that_is_the_broken_path(self):
        assert PatientInfoGeoPoint.country_code_by_country_code_or_name('Afghanistan') == 'AF'

    def test_a_country_further_down_still_resolves(self):
        """Non-vacuity: a fix that returned the first code for everything
        would pass the two above."""
        assert PatientInfoGeoPoint.country_code_by_country_code_or_name('Germany') == 'DE'

    def test_a_name_nobody_has_still_resolves_to_nothing(self):
        assert PatientInfoGeoPoint.country_code_by_country_code_or_name('Atlantis') is None

    @pytest.mark.django_db
    def test_the_patient_keeps_their_country(self):
        patient = _build_in_memory({'disease': 'multiple myeloma', 'country': 'Afghanistan'})
        assert patient.country == 'Afghanistan'
