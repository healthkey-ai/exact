"""
Tests for _normalize_structured_json_fields in resolve.py.

Guards the invariant that PatientInfo.later_therapies / supportive_therapies are
list-of-dicts and genetic_mutations contains only dicts before downstream
consumers (matcher, scoring, trial details) iterate items and call .get(...).
"""
import pytest

from trials.services.patient_info.resolve import (
    _build_in_memory,
    _normalize_structured_json_fields,
)


class TestNormalizeStructuredJsonFields:
    def test_bare_strings_in_supportive_therapies_coerced_to_dicts(self):
        data = {'supportive_therapies': ['Dexamethasone', 'Methotrexate']}
        _normalize_structured_json_fields(data)
        assert data['supportive_therapies'] == [
            {'therapy': 'Dexamethasone'},
            {'therapy': 'Methotrexate'},
        ]

    def test_bare_strings_in_later_therapies_coerced_to_dicts(self):
        data = {'later_therapies': ['Rituximab']}
        _normalize_structured_json_fields(data)
        assert data['later_therapies'] == [{'therapy': 'Rituximab'}]

    def test_properly_shaped_dicts_pass_through_unchanged(self):
        item = {'therapy': 'rituximab', 'startDate': '2023-01-01', 'endDate': '2023-06-01'}
        data = {'later_therapies': [item]}
        _normalize_structured_json_fields(data)
        assert data['later_therapies'] == [item]

    def test_mixed_list_keeps_dicts_coerces_strings_drops_other(self):
        data = {'later_therapies': [{'therapy': 'a'}, 'b', None, 42, '  c  ']}
        _normalize_structured_json_fields(data)
        assert data['later_therapies'] == [
            {'therapy': 'a'},
            {'therapy': 'b'},
            {'therapy': 'c'},  # whitespace trimmed
        ]

    def test_non_list_value_collapses_to_empty_list(self):
        data = {'later_therapies': 'oops a string'}
        _normalize_structured_json_fields(data)
        assert data['later_therapies'] == []

    def test_empty_and_whitespace_strings_dropped(self):
        data = {'supportive_therapies': ['', '   ', 'real']}
        _normalize_structured_json_fields(data)
        assert data['supportive_therapies'] == [{'therapy': 'real'}]

    def test_none_value_passes_through_untouched(self):
        data = {'later_therapies': None, 'supportive_therapies': None, 'genetic_mutations': None}
        _normalize_structured_json_fields(data)
        assert data == {'later_therapies': None, 'supportive_therapies': None, 'genetic_mutations': None}

    def test_missing_keys_are_noop(self):
        data = {}
        _normalize_structured_json_fields(data)
        assert data == {}

    def test_empty_list_stays_empty(self):
        data = {'later_therapies': [], 'supportive_therapies': [], 'genetic_mutations': []}
        _normalize_structured_json_fields(data)
        assert data == {'later_therapies': [], 'supportive_therapies': [], 'genetic_mutations': []}

    def test_genetic_mutations_drops_non_dict_items(self):
        data = {'genetic_mutations': [{'gene': 'TP53'}, 'TP53', None, 42]}
        _normalize_structured_json_fields(data)
        assert data['genetic_mutations'] == [{'gene': 'TP53'}]

    def test_genetic_mutations_non_list_collapses_to_empty(self):
        data = {'genetic_mutations': 'oops'}
        _normalize_structured_json_fields(data)
        assert data['genetic_mutations'] == []

    def test_unrelated_keys_left_alone(self):
        data = {'patient_age': 65, 'disease': 'multiple myeloma', 'later_therapies': ['x']}
        _normalize_structured_json_fields(data)
        assert data['patient_age'] == 65
        assert data['disease'] == 'multiple myeloma'
        assert data['later_therapies'] == [{'therapy': 'x'}]


class TestBuildInMemoryRegression:
    """End-to-end regression — the crash reported for person_id=20258.

    Before the fix, normalize_patient_info → stem_cell_transplant_history_from_therapy_lines
    iterated patient_info.later_therapies and called .get('therapy') on each item,
    raising AttributeError when items were bare strings. The validator now reshapes
    items before PatientInfo construction.
    """

    @pytest.mark.django_db
    def test_bare_string_later_therapies_does_not_crash(self):
        data = {
            'disease': 'multiple myeloma',
            'prior_therapy': 'More than two lines of therapy',
            'later_therapies': ['Bortezomib', 'Lenalidomide'],
        }
        pi = _build_in_memory(data)
        assert pi.later_therapies == [{'therapy': 'Bortezomib'}, {'therapy': 'Lenalidomide'}]

    @pytest.mark.django_db
    def test_bare_string_supportive_therapies_does_not_crash(self):
        data = {
            'disease': 'multiple myeloma',
            'prior_therapy': 'More than two lines of therapy',
            'supportive_therapies': ['Dexamethasone'],
        }
        pi = _build_in_memory(data)
        assert pi.supportive_therapies == [{'therapy': 'Dexamethasone'}]

    @pytest.mark.django_db
    def test_non_dict_genetic_mutations_dropped(self):
        data = {
            'disease': 'multiple myeloma',
            'genetic_mutations': [{'gene': 'tp53'}, 'TP53', None],
        }
        pi = _build_in_memory(data)
        assert pi.genetic_mutations == [{'gene': 'tp53'}]
