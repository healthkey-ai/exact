"""Structure-invariant tests for MarkersMapper (#30).

The mapper feeds `LoadMarkers` which seeds the `Marker` model used by
the matcher's cytogenicMarkers / molecularMarkers eligibility checks.
"""
from trials.services.markers_mapper import MarkersMapper


class TestCategories:
    def test_categories_returns_expected_pair(self):
        # Two categories — cytogenic and molecular — feed the
        # `MARKER_CATEGORIES` config and the disease-aware option lists
        # (`_CYTOGENIC_DISEASES` / `_MOLECULAR_DISEASES` in #63).
        assert MarkersMapper().categories() == {
            'cytogenic': 'Cytogenic Markers',
            'molecular': 'Molecular Markers',
        }


class TestData:
    def setup_method(self):
        self.mapper = MarkersMapper()
        self.data = self.mapper.data()
        self.known_category_titles = set(self.mapper.categories().values())

    def test_returns_non_empty_dict(self):
        assert isinstance(self.data, dict)
        assert len(self.data) > 0

    def test_every_entry_has_name_and_categories(self):
        for code, entry in self.data.items():
            assert 'name' in entry, code
            assert 'categories' in entry, code
            assert isinstance(entry['name'], str) and entry['name'], code
            assert isinstance(entry['categories'], list) and entry['categories'], code

    def test_entry_category_titles_are_subset_of_declared(self):
        """Every category title in a per-entry `categories` list must be
        one of the titles in `categories()`. Drift would silently seed
        markers under non-existent category rows.
        """
        for code, entry in self.data.items():
            extra = set(entry['categories']) - self.known_category_titles
            assert not extra, f'{code}: unknown category titles {sorted(extra)}'

    def test_known_entry_del17p13_present(self):
        # Spot-check: del(17p13) is the headline TP53-deletion marker.
        assert 'del17p13' in self.data
        entry = self.data['del17p13']
        assert 'Cytogenic Markers' in entry['categories']
