"""Unit tests for AttributeNames helpers (#30).

These three staticmethods turn attribute names between snake_case,
camelCase, and human-readable text. They power matchers, serializers,
and the form-settings layer — drift here would cascade into per-attr
labels and JSON keys without an obvious failure.
"""
import pytest

from trials.services.attribute_names import ATTRIBUTE_NAME_MAPPING, AttributeNames


class TestGetBySnakeCase:
    @pytest.mark.parametrize('snake,camel', [
        ('age_low_limit', 'ageMin'),
        ('age_high_limit', 'ageMax'),
        ('kappa_flc', 'kappaFLC'),
        ('lambda_flc', 'lambdaFLC'),
        ('meets_crab', 'meetsCRAB'),
        ('meets_slim', 'meetsSLIM'),
        ('meets_gelf', 'meetsGELF'),
    ])
    def test_explicit_mapping_takes_precedence(self, snake, camel):
        assert AttributeNames.get_by_snake_case(snake) == camel

    @pytest.mark.parametrize('snake,camel', [
        ('disease', 'disease'),
        ('treatment_refractory_status', 'treatmentRefractoryStatus'),
        ('hr_status', 'hrStatus'),
    ])
    def test_falls_back_to_inflection_camelize(self, snake, camel):
        assert AttributeNames.get_by_snake_case(snake) == camel


class TestGetByCamelCase:
    @pytest.mark.parametrize('camel,snake', [
        ('ageMin', 'age_low_limit'),
        ('ageMax', 'age_high_limit'),
        ('kappaFLC', 'kappa_flc'),
        ('meetsCRAB', 'meets_crab'),
        ('meetsGELF', 'meets_gelf'),
    ])
    def test_explicit_mapping_takes_precedence(self, camel, snake):
        assert AttributeNames.get_by_camel_case(camel) == snake

    @pytest.mark.parametrize('camel,snake', [
        ('disease', 'disease'),
        ('treatmentRefractoryStatus', 'treatment_refractory_status'),
        ('hrStatus', 'hr_status'),
    ])
    def test_falls_back_to_inflection_underscore(self, camel, snake):
        assert AttributeNames.get_by_camel_case(camel) == snake

    def test_explicit_mapping_round_trips_to_self(self):
        """Every explicit map entry should round-trip:
        snake → camel → snake should always equal snake.
        """
        for snake, camel in ATTRIBUTE_NAME_MAPPING.items():
            assert AttributeNames.get_by_camel_case(camel) == snake, snake
            assert AttributeNames.get_by_snake_case(snake) == camel, camel


class TestHumanize:
    @pytest.mark.parametrize('raw,expected', [
        ('contraceptiveUse', 'Contraceptive Use'),
        ('kappaFLC', 'Kappa FLC'),
        ('age_minimum', 'Age Minimum'),
        ('Lambda FLC', 'Lambda FLC'),
        ('disease', 'Disease'),
    ])
    def test_humanize_title_case(self, raw, expected):
        assert AttributeNames.humanize(raw) == expected

    def test_humanize_title_false_lowercases_words(self):
        # Acronyms are preserved even when title=False.
        result = AttributeNames.humanize('kappaFLC', title=False)
        assert result == 'kappa FLC'

    def test_humanize_strips_trailing_id_suffix(self):
        assert AttributeNames.humanize('therapy_id') == 'Therapy'

    def test_humanize_preserves_acronym_runs(self):
        # ECOG should survive as 'ECOG', not 'Ecog'.
        assert AttributeNames.humanize('patientECOG') == 'Patient ECOG'
