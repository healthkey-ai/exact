"""
Smoke tests for MCL-specific value_options properties (#40).

Each MCL option list must be non-empty and present in the central
get_all_options() registry so /form-settings/ exposes them to the frontend.
"""
import pytest

from trials.services.value_options import ValueOptions


class TestMclStaticEnums:
    """Static enum properties — no DB required."""

    def test_mipi_risks_has_three_tiers(self):
        opts = ValueOptions().mipi_risks
        assert set(opts.keys()) == {'low', 'intermediate', 'high'}

    def test_mipi_c_risks_has_four_tiers(self):
        opts = ValueOptions().mipi_c_risks
        assert set(opts.keys()) == {'low', 'low_intermediate', 'high_intermediate', 'high'}

    def test_disease_behaviors_mcl_non_empty(self):
        opts = ValueOptions().disease_behaviors_mcl
        assert 'indolent' in opts
        assert 'aggressive' in opts

    def test_disease_subtypes_mcl_non_empty(self):
        opts = ValueOptions().disease_subtypes_mcl
        assert {'ismcn', 'cmcl', 'nnmcl'}.issubset(opts.keys())


class TestMclDbBackedOptions:
    """DB-backed properties — require LoadMclOptions seeding from conftest."""

    @pytest.mark.django_db
    def test_morphologic_variants_non_empty(self):
        opts = ValueOptions().morphologic_variants
        assert {'classic', 'blastoid', 'pleomorphic'}.issubset(opts.keys())

    @pytest.mark.django_db
    def test_protein_expressions_mcl_non_empty(self):
        opts = ValueOptions().protein_expressions_mcl
        # MCL panel: cyclin_d1, sox11, cd10, bcl6 (each +/-)
        assert 'cyclin_d1_plus_ve' in opts
        assert 'sox11_plus_ve' in opts


class TestMclFormSettingsRegistry:
    """Central options dict must expose every new MCL key."""

    @pytest.mark.django_db
    def test_all_mcl_keys_registered(self):
        all_opts = ValueOptions().get_all_options()
        expected_keys = {
            'morphologicVariants',
            'mipiRisks',
            'mipiCRisks',
            'diseaseBehaviorsMcl',
            'diseaseSubtypesMcl',
            'proteinExpressionsMcl',
            'therapiesFirstLineMcl',
            'therapiesSecondLineMcl',
            'therapiesLaterLineMcl',
            'supportiveTherapiesMcl',
            'concomitantMedicationsMcl',
            'stagesMcl',
        }
        missing = expected_keys - set(all_opts.keys())
        assert not missing, f'MCL keys not in get_all_options(): {missing}'

    @pytest.mark.django_db
    def test_mcl_keys_have_options_field(self):
        all_opts = ValueOptions().get_all_options()
        for key in ('morphologicVariants', 'mipiRisks', 'mipiCRisks',
                    'diseaseBehaviorsMcl', 'diseaseSubtypesMcl', 'stagesMcl'):
            assert 'options' in all_opts[key]
            assert isinstance(all_opts[key]['options'], list)
            assert len(all_opts[key]['options']) > 0, f'{key} options list is empty'


class TestExistingDiseasesUnchanged:
    """Acceptance: existing disease enums unchanged."""

    @pytest.mark.django_db
    def test_cll_keys_still_present(self):
        all_opts = ValueOptions().get_all_options()
        for key in ('therapiesCll', 'supportiveTherapiesCll', 'stagesCll',
                    'binetStages', 'proteinExpressions', 'diseaseActivities'):
            assert key in all_opts
