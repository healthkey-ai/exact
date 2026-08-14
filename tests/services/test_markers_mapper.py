"""Structure-invariant tests for MarkersMapper (#30).

The mapper feeds `LoadMarkers`, which seeds the `CytogenicMarker` /
`MolecularMarker` catalogs used by the matcher's cytogenicMarkers /
molecularMarkers eligibility checks. Mirrors CB's mapper shape: one
canonical `_MARKERS` definition table plus two ordered code lists.
"""
from trials.services.markers_mapper import MarkersMapper


class TestCatalogs:
    def setup_method(self):
        self.mapper = MarkersMapper()
        self.cytogenic = self.mapper.cytogenic()
        self.molecular = self.mapper.molecular()

    def test_both_catalogs_non_empty(self):
        assert isinstance(self.cytogenic, dict) and len(self.cytogenic) > 0
        assert isinstance(self.molecular, dict) and len(self.molecular) > 0

    def test_every_entry_has_name_and_description(self):
        for catalog in (self.cytogenic, self.molecular):
            for code, entry in catalog.items():
                assert isinstance(entry.get('name'), str) and entry['name'], code
                assert 'description' in entry, code

    def test_codes_resolve_to_the_shared_definition_table(self):
        """Both catalogs project the same `_MARKERS` table — a code listed in
        an ordered list but missing from the table would KeyError at seed time.
        """
        for code, entry in {**self.cytogenic, **self.molecular}.items():
            assert entry is MarkersMapper._MARKERS[code], code

    def test_catalogs_overlap_but_are_not_duplicates(self):
        # A marker can be clinically relevant as both cytogenic and molecular,
        # so the lists overlap — but neither may be a copy of the other, which
        # would mean an ordered list was edited without its counterpart.
        assert set(self.cytogenic) & set(self.molecular)
        assert set(self.cytogenic) != set(self.molecular)

    def test_known_entry_del17p13_present(self):
        # Spot-check: del(17p13) is the headline TP53-deletion marker.
        assert 'del17p13' in self.cytogenic
        assert self.cytogenic['del17p13']['name']
