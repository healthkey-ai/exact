"""
Regression guards for #58 / #59 / #61 — dropdown options that a frontend
needs to expose "no marker / no neuropathy / no planned therapy" as an
explicit, selectable choice.

All three production fixes are already in EXACT (carried forward from CB
ports). Without these tests, a future change that drops any of the
sentinel options (e.g. removing the `{'none': 'None', ...}` prepend) would
land silently and re-introduce the original UX bug where patients had no
way to opt out.
"""
import pytest

from trials.services.value_options import ValueOptions


class TestMarkerDropdownNoneOption:
    """#58 / CB #4216: cytogenic and molecular marker dropdowns must let
    patients explicitly select 'None' so trials requiring no marker can
    match instead of falling through to Potential.
    """

    @pytest.mark.django_db
    def test_cytogenic_markers_include_none(self):
        opts = ValueOptions().cytogenic_markers
        assert 'none' in opts
        assert opts['none'] == 'None'

    @pytest.mark.django_db
    def test_molecular_markers_include_none(self):
        opts = ValueOptions().molecular_markers
        assert 'none' in opts
        assert opts['none'] == 'None'

    @pytest.mark.django_db
    def test_none_is_first_entry_in_both_marker_dropdowns(self):
        # Insertion order matters: 'none' should appear at the top of the
        # multiselect so patients see it before the long marker list.
        assert list(ValueOptions().cytogenic_markers.keys())[0] == 'none'
        assert list(ValueOptions().molecular_markers.keys())[0] == 'none'


class TestPeripheralNeuropathyGradeOptions:
    """#59 / CB #4307: dropdown must offer Grade 0 (no neuropathy) and
    label the empty value 'Unknown', not 'None' — otherwise patients
    confused 'None' with 'Grade 0' and selected the wrong thing.
    """

    def test_unknown_sentinel_uses_unknown_label(self):
        opts = ValueOptions().peripheral_neuropathy_grades
        assert opts[''] == 'Unknown'

    def test_grade_zero_is_a_distinct_selectable_option(self):
        opts = ValueOptions().peripheral_neuropathy_grades
        assert '0' in opts
        assert opts['0'] == 'Grade 0'

    def test_full_grade_set(self):
        opts = ValueOptions().peripheral_neuropathy_grades
        assert set(opts.keys()) == {'', '0', '1', '2', '3', '4'}

    def test_grade_5_is_not_offered(self):
        """Regression for #57 / CB 887be4d9: Grade 5 (CTCAE 'Death') is not
        a self-reportable patient state. Removed from the dropdown."""
        opts = ValueOptions().peripheral_neuropathy_grades
        assert '5' not in opts


class TestTumorGradeOptions:
    """#56 / CB a8fd82c2: WHO FL grading is 1/2/3A/3B only — Grade 4 is
    clinically invalid and must not appear in the dropdown.
    """

    def test_full_grade_set(self):
        opts = ValueOptions().tumor_grades
        assert set(opts.keys()) == {'', '10', '20', '30', '31', '32'}

    def test_grade_4_is_not_offered(self):
        """Grade 4 (code '40') was removed in PR #66 per WHO FL grading."""
        opts = ValueOptions().tumor_grades
        assert '40' not in opts
        # And no '4' fallback either — the only valid grade-3 codes are the
        # 3 / 3A / 3B subgrades (30 / 31 / 32).
        assert '4' not in opts

    def test_3a_and_3b_subgrades_are_present(self):
        opts = ValueOptions().tumor_grades
        assert opts['31'] == 'Grade 3A'
        assert opts['32'] == 'Grade 3B'


class TestPlannedTherapiesNoneOption:
    """#61 / CB 33f7525a: planned-therapies multiselect must let patients
    opt out by selecting 'No planned therapy' — otherwise trials that
    require no planned therapy aren't reachable.

    LoadPlannedTherapyOptions only seeds MM/FL/BC connections (no CLL/MCL),
    so the parametrize is limited to the diseases that actually exercise
    the prepend-then-spread merge against real items.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease_code', ['MM', 'FL', 'BC'])
    def test_none_option_present_and_first_for_seeded_diseases(self, disease_code):
        opts = ValueOptions().planned_therapies(disease_code)
        assert 'none' in opts
        assert opts['none'] == 'No planned therapy'
        # Real planned therapies must be present too (otherwise the test is
        # tautological: a dict of length 1 trivially has 'none' first).
        assert len(opts) > 1, (
            f'{disease_code} should have seeded planned therapies; got {list(opts.keys())}'
        )
        # Insertion order: 'none' surfaces above the seeded therapies so the
        # frontend renders the opt-out as the first multiselect option.
        assert list(opts.keys())[0] == 'none'

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease_code', ['CLL', 'MCL'])
    def test_none_option_present_for_unseeded_diseases(self, disease_code):
        # CLL and MCL have no planned-therapy seed data today, but the opt-out
        # must still appear so the frontend dropdown isn't empty.
        opts = ValueOptions().planned_therapies(disease_code)
        assert opts == {'none': 'No planned therapy'}
