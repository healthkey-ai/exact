"""Structure-invariant tests for ConcomitantMedicationsMapper (#30).

The mapper is the single source of truth for the concomitant-medications
taxonomy seeded by `LoadConcomitantMedications`. Drift between the
diseases() declaration and the per-entry `diseases` lists would silently
seed medications under non-existent disease codes.
"""
from trials.services.concomitant_medications_mapper import ConcomitantMedicationsMapper


class TestDiseases:
    def test_diseases_returns_expected_codes(self):
        mapper = ConcomitantMedicationsMapper()
        assert mapper.diseases() == {
            'MM': 'Multiple myeloma',
            'FL': 'Follicular lymphoma',
            'BC': 'Breast cancer',
            'CLL': 'Chronic lymphocytic leukemia',
        }


class TestData:
    def setup_method(self):
        self.mapper = ConcomitantMedicationsMapper()
        self.data = self.mapper.data()
        self.known_disease_codes = set(self.mapper.diseases().keys())

    def test_returns_non_empty_dict(self):
        assert isinstance(self.data, dict)
        assert len(self.data) > 0

    def test_every_entry_has_name_and_diseases(self):
        for code, entry in self.data.items():
            assert 'name' in entry, code
            assert 'diseases' in entry, code
            assert isinstance(entry['name'], str) and entry['name'], code
            assert isinstance(entry['diseases'], list), code

    def test_entry_disease_codes_are_subset_of_declared(self):
        """Every code in a per-entry `diseases` list must be one of the
        codes returned by `diseases()`. Drift here would silently seed
        medications under non-existent disease rows.
        """
        for code, entry in self.data.items():
            extra = set(entry['diseases']) - self.known_disease_codes
            assert not extra, f'{code}: unknown disease codes {sorted(extra)}'

    def test_entry_keys_are_unique(self):
        # data() returns a dict so this is structurally guaranteed,
        # but assert anyway to flag any future merge that doesn't.
        assert len(self.data) == len(set(self.data.keys()))

    def test_known_entry_investigational_agents_present(self):
        assert 'investigational_agents' in self.data
        entry = self.data['investigational_agents']
        assert entry['name'] == 'Investigational Agents'
        assert set(entry['diseases']) == self.known_disease_codes
