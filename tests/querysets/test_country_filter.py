"""The country filter matches what the options list actually offers (#430).

`allCountries` is built from `PreferredCountry`, whose `code` is what the UI
sends — `US`, `GB`. `Country` rows carry only a title, so `by_location`
matched nothing and, returning `self`, narrowed nothing. The control was
decoration.

The trap underneath it: `US` names "United States of America", and the catalog
ALSO holds "United States" — 18,365 site links against the other's one in the
real corpus. Translating the code and stopping at the first match would have
narrowed a US search to a single trial, which is worse than not filtering.
"""
import pytest

from trials.models import Country, Location, LocationTrial, PreferredCountry, State
from tests.factories import TrialFactory


def _sited(study_id, country, state=None):
    trial = TrialFactory(study_id=study_id)
    LocationTrial.objects.create(
        trial=trial,
        location=Location.objects.create(
            city=study_id, title=f'Site {study_id}', country=country, state=state,
        ),
    )
    return trial


def _found(country, state=None):
    from trials.models import Trial
    return {
        t.study_id
        for t in Trial.objects.all().by_location(country, state).only('study_id')
    }


@pytest.fixture
def catalog(db):
    """The shape the real corpus has: one country split across two titles, the
    options list naming the emptier of the two."""
    big = Country.objects.create(title='United States')
    stray = Country.objects.create(title='United States of America')
    germany = Country.objects.create(title='Germany')
    PreferredCountry.objects.create(code='US', title='United States of America')
    PreferredCountry.objects.create(code='DE', title='Germany')

    _sited('US_MAIN_1', big)
    _sited('US_MAIN_2', big)
    _sited('US_STRAY', stray)
    _sited('DE_ONE', germany)
    TrialFactory(study_id='NO_SITE')
    return {'big': big, 'stray': stray, 'germany': germany}


@pytest.mark.django_db
class TestTheCodeTheUiActuallySends:
    def test_a_code_narrows(self, catalog):
        assert _found('DE') == {'DE_ONE'}

    def test_case_and_whitespace_do_not_defeat_it(self, catalog):
        assert _found(' de ') == {'DE_ONE'}

    def test_a_title_still_works(self, catalog):
        """A caller who was not reading the options list is unaffected."""
        assert _found('Germany') == {'DE_ONE'}


@pytest.mark.django_db
class TestACountrySplitAcrossTwoTitles:
    def test_every_spelling_finds_every_site(self, catalog):
        """The whole point. Resolving `US` to its options-list title alone
        would return `US_STRAY` — one trial out of three — which reads as a
        working filter and is the worst outcome available."""
        for spelling in ('US', 'us', 'United States', 'United States of America', 'usa'):
            assert _found(spelling) == {'US_MAIN_1', 'US_MAIN_2', 'US_STRAY'}, spelling

    def test_it_does_not_drag_in_another_country(self, catalog):
        assert 'DE_ONE' not in _found('US')


@pytest.mark.django_db
class TestWhatStillMeansNoFilter:
    def test_an_unknown_country_narrows_nothing(self, catalog):
        """Unchanged, and deliberately: a value this cannot resolve has always
        meant "no filter" rather than "no results"."""
        assert _found('Atlantis') == {'US_MAIN_1', 'US_MAIN_2', 'US_STRAY', 'DE_ONE', 'NO_SITE'}

    def test_and_so_does_an_empty_one(self, catalog):
        for value in ('', '   ', None):
            assert _found(value) == {
                'US_MAIN_1', 'US_MAIN_2', 'US_STRAY', 'DE_ONE', 'NO_SITE'
            }, value


@pytest.mark.django_db
class TestTheStateStillNarrowsWithin:
    def test_a_state_is_looked_up_across_both_rows(self, catalog):
        """The state lookup was scoped to the single `Country` row the old code
        picked, so a state filed under the other title could not be found."""
        ny = State.objects.create(country=catalog['stray'], title='New York')
        _sited('US_NY', catalog['stray'], state=ny)

        assert _found('US', 'New York') == {'US_NY'}

    def test_a_state_split_across_both_rows_is_found_in_both(self, catalog):
        """A country split across two catalog rows has its states split too,
        so "New York" exists under each — and picking one arbitrarily drops
        the trials filed under the other. The same mistake as `.first()` on the
        country, one level down."""
        ny_stray = State.objects.create(country=catalog['stray'], title='New York')
        ny_big = State.objects.create(country=catalog['big'], title='New York')
        _sited('NY_UNDER_STRAY', catalog['stray'], state=ny_stray)
        _sited('NY_UNDER_BIG', catalog['big'], state=ny_big)

        assert _found('US', 'New York') == {'NY_UNDER_STRAY', 'NY_UNDER_BIG'}

    def test_an_unknown_state_falls_back_to_the_country(self, catalog):
        assert _found('US', 'Atlantis') == {'US_MAIN_1', 'US_MAIN_2', 'US_STRAY'}


@pytest.mark.django_db
class TestTheOtherSplitCountries:
    """The US was not a quirk. Nine countries are split across titles in the
    corpus, and once the code translation works, an ungrouped one narrows to
    PART of a country while looking like it worked.

    Grouped by an explicit table rather than a rule: stripping ", Republic of"
    or a parenthetical would merge `Korea, South` with a `Korea, Democratic
    People's Republic of` that a later import may add — two countries, one
    answer, in a clinical tool.
    """

    @pytest.mark.parametrize('titles,code,options_title', [
        (['Czechia', 'Czech Republic'], 'CZ', 'Czechia'),
        (['Russian Federation', 'Russia'], 'RU', 'Russian Federation'),
        (['Turkey (Türkiye)', 'Turkey'], 'TR', 'Turkey (Türkiye)'),
        (['Korea, Republic of', 'South Korea', 'Korea, South'], 'KR', 'Korea, Republic of'),
        (['Iran, Islamic Republic of', 'Iran'], 'IR', 'Iran, Islamic Republic of'),
    ])
    def test_every_spelling_finds_every_site(self, db, titles, code, options_title):
        PreferredCountry.objects.create(code=code, title=options_title)
        expected = set()
        for i, title in enumerate(titles):
            country = Country.objects.create(title=title)
            study_id = f'{code}_{i}'
            _sited(study_id, country)
            expected.add(study_id)

        # The code, and every spelling the catalog holds.
        assert _found(code) == expected
        for title in titles:
            assert _found(title) == expected, title

    def test_it_does_not_merge_two_different_countries(self, db):
        """The reason this is a table. North and South Korea share a prefix and
        a naming convention, and nothing here may put them together."""
        south = Country.objects.create(title='Korea, Republic of')
        north = Country.objects.create(title="Korea, Democratic People's Republic of")
        _sited('SOUTH', south)
        _sited('NORTH', north)

        assert _found('South Korea') == {'SOUTH'}
        assert _found("Korea, Democratic People's Republic of") == {'NORTH'}


@pytest.mark.django_db
class TestTheOptionThatIsNotACountry:
    def test_other_narrows_nothing_and_that_is_a_decision(self, catalog):
        """`other` is a real, shipped option (`load_preferred_countries_options`
        creates it). It resolves to no catalog row, so the search is
        unnarrowed while its 30 neighbours narrow — the #430 symptom preserved
        for one value.

        Left unnarrowed deliberately: "Other" cannot be expressed as a set of
        countries without inverting the catalog, and quietly ignoring it is the
        honest answer until someone decides what it should mean."""
        PreferredCountry.objects.create(code='other', title='Other')

        assert _found('other') == {
            'US_MAIN_1', 'US_MAIN_2', 'US_STRAY', 'DE_ONE', 'NO_SITE'
        }


@pytest.mark.django_db
class TestACountryTheOptionsListDoesNotCarry:
    def test_a_plain_title_still_works(self, db):
        """The claim that "a caller who was not reading the options list is
        unaffected" — asserted on a country with NO `PreferredCountry` row, so
        it exercises the title path rather than resolving through the code
        table by accident."""
        canada = Country.objects.create(title='Canada')
        _sited('CA_ONE', canada)
        _sited('DE_ONE', Country.objects.create(title='Germany'))

        assert _found('Canada') == {'CA_ONE'}
        assert _found('canada') == {'CA_ONE'}


@pytest.mark.django_db
class TestACountryWhoseNameIsNotASCII:
    """This database is `LC_CTYPE=C`, where Postgres `upper()` leaves non-ASCII
    alone and Python's `.lower()` does not. Lowercasing the value in Python
    before an `iexact` therefore does NOT commute: `Å` becomes `å`, `upper()`
    hands it back unchanged, and the catalog's own `Å` no longer matches. The
    exact spelling the catalog ships is the one a caller is most likely to
    send, so this is the common path, not an edge."""

    def test_the_catalogs_own_spelling_finds_its_trials(self, db):
        aland = Country.objects.create(title='Åland Islands')
        _sited('AX_ONE', aland)
        _sited('DE_ONE', Country.objects.create(title='Germany'))

        assert _found('Åland Islands') == {'AX_ONE'}

    def test_and_so_does_shouting_it(self, db):
        aland = Country.objects.create(title='Åland Islands')
        _sited('AX_ONE', aland)
        _sited('DE_ONE', Country.objects.create(title='Germany'))

        assert _found('ÅLAND ISLANDS') == {'AX_ONE'}
