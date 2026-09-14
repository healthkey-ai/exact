"""CTOMOP's transplant rows are not a 500 (#141).

CTOMOP's loader writes one dict per line of therapy:

    [{"line_number": 1, "procedures": "Autologous stem cell transplant"}]

while everything downstream expects a list of hashable vocabulary codes.
`eligible_for_stem_cell_transplant_history` does
`SCT_HISTORY_EXCLUDED_MAPPING.get(item, [item])`, which raises
`TypeError: unhashable type: 'dict'` — a 500 on every patient-context
endpoint for any patient CTOMOP knows a transplant for. Both BigQuery-sourced
and seeded patients carry this shape; it surfaced when a dev harness piped
CTOMOP rows through `/normalize-ctomop-row/` and the match endpoint, and
stripping this one field made the request work.

Normalised at the boundary rather than in the matcher: the matcher and the
queryset are ~95% shared with CancerBot, which never sees this shape.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory
from trials.services.patient_info.resolve import _build_in_memory


AUTOLOGOUS = {'line_number': 1, 'procedures': 'Autologous stem cell transplant'}
ALLOGENEIC = {'line_number': 3, 'procedures': 'Allogeneic stem cell transplant'}
UNREADABLE = {'line_number': 2, 'procedures': 'Transfusion of stem cells'}


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='sct-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _built(rows):
    # `prior_therapy` set so `_normalize_therapy_lines` does not clear the
    # field out from under the assertion — it sets it to 'None' when there is
    # no prior therapy, which is a different question from this one.
    return _build_in_memory(
        {'prior_therapy': 'Two lines', 'stem_cell_transplant_history': rows}
    )


class TestTheStructuredShapeBecomesCodes:
    def test_one_autologous_row(self):
        assert _built([AUTOLOGOUS]).stem_cell_transplant_history == ['completedASCT']

    def test_both_types(self):
        assert _built([AUTOLOGOUS, ALLOGENEIC]).stem_cell_transplant_history == [
            'completedASCT', 'completedAllogeneicSCT',
        ]

    def test_a_comma_separated_string_naming_both(self):
        """`procedures` is free text from `AILineOfTherapySummary.procedures`,
        documented as "Procedure names, comma-separated"."""
        rows = [{'line_number': 1,
                 'procedures': 'Autologous stem cell transplant, Allogeneic stem cell transplant'}]
        assert _built(rows).stem_cell_transplant_history == [
            'completedASCT', 'completedAllogeneicSCT',
        ]

    def test_the_same_type_twice_is_listed_once(self):
        rows = [AUTOLOGOUS, {'line_number': 2, 'procedures': 'ASCT'}]
        assert _built(rows).stem_cell_transplant_history == ['completedASCT']

    @pytest.mark.parametrize('name,expected', [
        ('Myeloablative Allotransplant', 'completedAllogeneicSCT'),
        ('Allograft of cord blood', 'completedAllogeneicSCT'),
        ('Allogenic transplant', 'completedAllogeneicSCT'),
        ('AHCT', 'completedASCT'),
        ('auto-SCT', 'completedASCT'),
    ])
    def test_names_that_do_not_spell_their_type_out(self, name, expected):
        """From HT's own `procedureMappings` vocabulary, via SoC's adapter:
        these name no type explicitly and are clinically unambiguous."""
        assert _built([{'line_number': 1, 'procedures': name}]).stem_cell_transplant_history == [expected]

    def test_a_list_of_codes_is_left_alone(self):
        """The canonical shape, which a non-CTOMOP caller sends."""
        assert _built(['completedASCT']).stem_cell_transplant_history == ['completedASCT']


class TestAnUnreadableRowDiscardsTheWholeHistory:
    """The important half.

    `procedures` is free text, so a row can say "Stem Cell Transplant" — true,
    and unclassifiable. Emitting the rows we DID classify would turn "an
    autologous transplant and something we could not read" into "an autologous
    transplant", and a trial EXCLUDING allogeneic transplants would go from
    unknown to eligible for a patient whose unreadable row may well have been
    allogeneic.
    """

    def test_a_partial_parse_yields_nothing(self):
        assert _built([AUTOLOGOUS, UNREADABLE]).stem_cell_transplant_history is None

    def test_order_does_not_matter(self):
        assert _built([UNREADABLE, AUTOLOGOUS]).stem_cell_transplant_history is None

    def test_a_row_with_no_procedure_name_counts_as_unreadable(self):
        """The loader only writes a row when `has_transplant` is true, so an
        empty name is a transplant we cannot classify, not an absence."""
        assert _built([AUTOLOGOUS, {'line_number': 2, 'procedures': ''}]).stem_cell_transplant_history is None

    def test_and_a_wholly_unreadable_history_too(self):
        assert _built([UNREADABLE]).stem_cell_transplant_history is None


@pytest.mark.django_db
class TestThroughARequest:
    def test_the_endpoint_answers_instead_of_500ing(self, authed_client):
        TrialFactory(disease='multiple myeloma')
        response = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma',
                              'prior_therapy': 'Two lines',
                              'stem_cell_transplant_history': [AUTOLOGOUS]}},
            format='json',
        )
        assert response.status_code == 200

    def test_and_the_history_actually_filters(self, authed_client):
        """Not just "no crash": the normalised codes have to REACH the
        queryset, or this is a 200 that ignores the patient's history.

        Asserted on a trial that EXCLUDES allogeneic transplants, because that
        is the only shape the code list actually drives —
        `eligible_for_stem_cell_transplant_history` reads
        `..._excluded__has_any_keys`, and touches
        `stem_cell_transplant_history_required` only for a "no SCT" value. My
        first version used a REQUIRED trial, which is kept whether the history
        arrives or not; review showed it passing with the normalisation forced
        to `None`.
        """
        excludes_allogeneic = TrialFactory(
            disease='multiple myeloma',
            stem_cell_transplant_history_excluded=['priorAllogeneicSCT'],
        )
        other = TrialFactory(disease='multiple myeloma')

        response = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma',
                              'prior_therapy': 'Two lines',
                              'stem_cell_transplant_history': [ALLOGENEIC]}},
            format='json',
        )
        assert response.status_code == 200
        ids = {t['trialId'] for t in response.data['results']}
        assert other.id in ids, 'the unrelated trial vanished; the fixture is wrong'
        assert excludes_allogeneic.id not in ids, (
            'a trial excluding allogeneic transplants was offered to a patient '
            "whose CTOMOP history records one — the codes are not reaching the "
            'queryset'
        )


class TestTextThatMeansSomethingOtherThanDone:
    """Substring matching cannot read a qualifier.

    "non-autologous transplant" contains "autologous"; "ASCT-ineligible"
    contains "asct". Classifying those as COMPLETED is worse than not
    classifying them: a patient ruled out for a transplant would be recorded
    as having had one, and a trial requiring one would accept them.

    They are discarded rather than mapped to the status codes this vocabulary
    also has (`ineligibleForASCT`, `preASCT`). The loader only writes a row
    when `has_transplant` is true — so if the row then says "ineligible", the
    two disagree, and picking a winner is the confident wrong answer this
    module exists to avoid.
    """

    @pytest.mark.parametrize('text', [
        # Each names a type, so the ordinary unclassifiable path cannot
        # account for it — two earlier cases ("Not a transplant candidate",
        # "Transplant workup") named none and were discarded either way, so
        # they tested nothing.
        'non-autologous transplant',
        'not allogeneic transplant',
        'ASCT-ineligible',
        'Eligible for ASCT',
        'pre-ASCT',
        'planned allogeneic transplant',
        'ASCT workup',
        'Under consideration for allogeneic transplant',
        'Evaluation for autologous transplant',
        'Intended allogeneic transplant',
        'Prior to ASCT',
    ])
    def test_it_is_discarded(self, text):
        assert _built([{'line_number': 1, 'procedures': text}]).stem_cell_transplant_history is None

    def test_and_it_discards_the_rows_beside_it(self):
        rows = [AUTOLOGOUS, {'line_number': 2, 'procedures': 'ASCT-ineligible'}]
        assert _built(rows).stem_cell_transplant_history is None

    @pytest.mark.parametrize('text', [
        'post-ASCT',
        'Relapsed post-ASCT',
        # The one that made the first version wrong: a standard name for a
        # transplant that DID happen. The negation attaches to
        # "myeloablative", not to "allogeneic".
        'Non-myeloablative allogeneic stem cell transplant',
        'Nonmyeloablative allogeneic SCT',
        'Allogeneic transplant, non-related donor',
        'Autologous SCT without maintenance',
    ])
    def test_but_these_did_happen(self, text):
        """Non-vacuity, and the line: being *after* a transplant means having
        had one. A qualifier list wide enough to swallow this would discard
        real histories."""
        history = _built([{'line_number': 1, 'procedures': text}]).stem_cell_transplant_history
        assert history, f'{text!r} was discarded; it names a transplant that happened'


class TestShapesThatAreNeitherCodesNorRows:
    """The `unhashable type` crash, in its other clothes.

    The structured-shape test asks "is any item a dict?", so a list of LISTS
    carries no dict and was waved through to the queryset — the same
    `TypeError`, with `list` where `dict` used to be.
    """

    @pytest.mark.parametrize('value', [
        [['nested']],
        [1, 2],
        [None],
        [{'line_number': 1, 'procedures': 'Autologous SCT'}, ['nested']],
    ])
    def test_they_are_discarded_rather_than_forwarded(self, value):
        history = _built(value).stem_cell_transplant_history
        assert history is None or all(isinstance(item, str) for item in history)

    def test_a_code_beside_a_row_survives(self):
        """Non-vacuity: a guard that discarded every mixed list would pass the
        four above and lose real data."""
        value = ['completedASCT', {'line_number': 2, 'procedures': 'Allogeneic SCT'}]
        assert _built(value).stem_cell_transplant_history == [
            'completedASCT', 'completedAllogeneicSCT',
        ]

    def test_but_a_string_that_is_not_a_code_discards_the_history(self):
        """An unrecognised string beside CTOMOP rows is a third thing nobody
        can account for. Keeping it would leave a value the matcher silently
        ignores inside a history this function is otherwise careful about."""
        value = ['not_a_code', {'line_number': 2, 'procedures': 'Allogeneic SCT'}]
        assert _built(value).stem_cell_transplant_history is None

    def test_a_pure_list_of_strings_is_still_left_alone(self):
        """The canonical shape from a caller who knows the vocabulary.
        Validating it here would be a contract change for every non-CTOMOP
        client rather than a fix for this one — so the leniency is asymmetric
        on purpose, and pinned so it reads as a decision."""
        assert _built(['whatever_they_sent']).stem_cell_transplant_history == [
            'whatever_they_sent'
        ]


class TestEveryPatternIsReachable:
    """A third of the table had no covering test — deleting `autograft`,
    `autotransplant`, `allo-sct` or `allohct` left the suite green."""

    @pytest.mark.parametrize('text,expected', [
        ('Autologous stem cell transplant', 'completedASCT'),
        ('Stem cell autograft', 'completedASCT'),
        ('Autotransplant', 'completedASCT'),
        ('auto-SCT', 'completedASCT'),
        ('AHCT', 'completedASCT'),
        ('ASCT', 'completedASCT'),
        ('Allogeneic stem cell transplant', 'completedAllogeneicSCT'),
        ('Allogenic transplant', 'completedAllogeneicSCT'),
        ('Allograft of cord blood', 'completedAllogeneicSCT'),
        ('Myeloablative Allotransplant', 'completedAllogeneicSCT'),
        ('allo-SCT', 'completedAllogeneicSCT'),
        ('alloHCT', 'completedAllogeneicSCT'),
    ])
    def test_each_name_classifies(self, text, expected):
        assert _built([{'line_number': 1, 'procedures': text}]).stem_cell_transplant_history == [
            expected
        ]

    def test_a_name_the_table_does_not_cover_is_discarded(self):
        """Non-vacuity for the twelve above, and a note on scope: this
        vocabulary has twelve codes and the table emits two. A tandem
        transplant named only as "Tandem transplant" is discarded rather than
        guessed at."""
        assert _built(
            [{'line_number': 1, 'procedures': 'Tandem transplant'}]
        ).stem_cell_transplant_history is None


class TestAShortAcronymIsAWordNotASubstring:
    """`asct`, `ahct` and `allohct` are three to seven letters long, and a
    substring test classifies a completed transplant wherever those letters
    happen to fall."""

    @pytest.mark.parametrize('text', ['Basctomy', 'xxahctxx', 'reallohctomy'])
    def test_letters_inside_another_word_do_not_count(self, text):
        assert _built([{'line_number': 1, 'procedures': text}]).stem_cell_transplant_history is None

    @pytest.mark.parametrize('text,expected', [
        ('ASCT', 'completedASCT'),
        ('Patient had ASCT.', 'completedASCT'),
        ('auto-SCT', 'completedASCT'),
        ('alloHCT', 'completedAllogeneicSCT'),
    ])
    def test_but_the_acronym_itself_still_counts(self, text, expected):
        """Non-vacuity: a boundary rule strict enough to reject the hyphenated
        forms would pass the three above and lose real histories."""
        assert _built([{'line_number': 1, 'procedures': text}]).stem_cell_transplant_history == [
            expected
        ]
