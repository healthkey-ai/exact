"""Structure-invariant tests for TherapiesMapper (#30).

The mapper is 1500 lines of static therapy regimens — exhaustively
testing every entry would just duplicate the data. These tests assert
the shape contract that `LoadTherapies` and the disease-aware therapy
line getters depend on.
"""
import pytest

from trials.services.therapies_mapper import TherapiesMapper


class TestData:
    def setup_method(self):
        self.mapper = TherapiesMapper()
        self.data = self.mapper.data()

    def test_returns_non_empty_dict(self):
        assert isinstance(self.data, dict)
        assert len(self.data) > 0

    def test_every_entry_has_required_keys(self):
        """Only `name` and `drugs` are universal. `short_name` is set on
        regimen entries but absent on simple-drug entries
        (e.g. `selinexor`, `asct`); `drugs` may be empty for entries
        that represent procedures or observation rather than drug
        regimens (e.g. `asct`, `ww`, `ifrt`).
        """
        for code, entry in self.data.items():
            assert 'name' in entry, code
            assert 'drugs' in entry, code
            assert isinstance(entry['name'], str) and entry['name'], code
            assert isinstance(entry['drugs'], list), code
            if 'short_name' in entry:
                assert isinstance(entry['short_name'], str) and entry['short_name'], code

    def test_drug_lists_contain_strings(self):
        for code, entry in self.data.items():
            for drug in entry['drugs']:
                assert isinstance(drug, str) and drug, f'{code}: empty/non-str drug {drug!r}'

    def test_codes_are_unique(self):
        assert len(self.data) == len(set(self.data.keys()))

    @pytest.mark.parametrize('code', ['vrd', 'dara_vrd', 'dara_rd'])
    def test_known_mm_regimen_present(self, code):
        # Spot-check: VRd and Dara-VRd variants are headline MM regimens.
        assert code in self.data


class TestDiseaseKeyedLineGetters:
    """The mapper exposes disease-keyed methods (e.g. `first_line_mm`)
    that return therapy lists for use by `LoadTherapies`. Smoke-test
    that the documented methods exist, return iterables of strings, and
    each entry resolves to a `data()` code.
    """

    @pytest.mark.parametrize('method_name', [
        'first_line_mm', 'first_line_fl', 'first_line_bc',
        'second_line_mm', 'second_line_fl', 'second_line_bc',
        'later_therapy_mm', 'later_therapy_fl', 'later_therapy_bc',
    ])
    def test_returns_iterable_of_known_codes(self, method_name):
        mapper = TherapiesMapper()
        codes = getattr(mapper, method_name)()
        assert codes, f'{method_name} returned empty'
        known = set(mapper.data().keys())
        unknown = [c for c in codes if c not in known]
        assert not unknown, (
            f'{method_name} returns codes not in data(): {unknown}'
        )
