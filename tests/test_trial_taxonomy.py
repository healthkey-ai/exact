"""Tests for the trial-type taxonomy (#42).

Two layers:

1. Pure-Python: TRIAL_TAXONOMY and ALL_TRIAL_TYPES import and have the
   structural invariants (no duplicate codes, MCL inclusions and
   exclusions match the clinical spec from issue #42).
2. DB-side: after the seed migration and the LoadTrialTaxonomy loader
   run (the latter from conftest, after Disease-seeding loaders),
   TrialType rows exist for every taxonomy entry and
   TrialTypeDiseaseConnection reflects the per-disease applicability —
   specifically MCL has the full expected set AND explicitly does NOT
   have PI3K Inhibitors.

The migration alone seeds TrialType rows but cannot seed connections
because Disease rows don't exist at migrate time. `LoadTrialTaxonomy`
(called from `tests/conftest.py` after the Disease loaders) closes that
gap idempotently.
"""
import pytest

from trials.models import Disease, TrialType
from trials.trial_taxonomy import ALL_TRIAL_TYPES, TRIAL_TAXONOMY


class TestTaxonomyStructure:
    def test_imports_work(self):
        # Acceptance line from issue #42.
        from trials.trial_taxonomy import (  # noqa: F401
            ALL_TRIAL_TYPES,
            TRIAL_TAXONOMY,
        )

    def test_all_trial_types_is_deduped_by_code(self):
        codes = [code for code, _, _ in ALL_TRIAL_TYPES]
        assert len(codes) == len(set(codes)), \
            f'duplicate codes in ALL_TRIAL_TYPES: {sorted(c for c in codes if codes.count(c) > 1)}'

    def test_no_duplicate_codes_across_categories(self):
        seen: dict[str, str] = {}
        for category, entries in TRIAL_TAXONOMY.items():
            for code, _, _ in entries:
                if code in seen:
                    pytest.fail(
                        f'code {code!r} appears in both {seen[code]!r} and {category!r}'
                    )
                seen[code] = category

    def test_all_trial_types_sorted_by_title(self):
        titles = [title for _, title, _ in ALL_TRIAL_TYPES]
        assert titles == sorted(titles)

    @pytest.mark.parametrize('expected_code', [
        # Spot-check core MCL inclusions per issue #42 / MCL clinical spec.
        'chemotherapy',
        'targeted_therapy_btk_inhibitors',
        'targeted_therapy_bcl2_inhibitors',
        'targeted_therapy_proteasome_inhibitors',
        'targeted_therapy_cdk46_inhibitors',
        'cellular_therapy_car_t',
        'stem_cell_transplant_autologous',
        'stem_cell_transplant_allogeneic',
        'radiation_therapy',
        'watchful_waiting',
    ])
    def test_mcl_included_in_expected_types(self, expected_code):
        entry = next((t for t in ALL_TRIAL_TYPES if t[0] == expected_code), None)
        assert entry is not None, f'taxonomy missing expected code {expected_code!r}'
        _, _, diseases = entry
        assert 'MCL' in diseases, f'MCL missing from {expected_code!r}: {diseases}'

    def test_mcl_explicitly_excluded_from_pi3k_inhibitors(self):
        """Per issue #42 spec: PI3K Inhibitors are not approved for MCL."""
        entry = next(
            (t for t in ALL_TRIAL_TYPES if t[0] == 'targeted_therapy_pi3k_inhibitors'),
            None,
        )
        assert entry is not None
        _, _, diseases = entry
        assert 'MCL' not in diseases, (
            f'MCL should NOT be in targeted_therapy_pi3k_inhibitors; '
            f'PI3K inhibitors are not approved for MCL. Got: {diseases}'
        )

    @pytest.mark.parametrize('breast_only_code', [
        'surgery',
        'hormone_therapy',
        'endocrine_therapy',
        'neoadjuvant_therapy',
        'adjuvant_therapy',
        'targeted_therapy_parp_inhibitors',
        'targeted_therapy_her2_inhibitors',
        'brca_testing',
    ])
    def test_breast_only_codes_exclude_mcl(self, breast_only_code):
        entry = next((t for t in ALL_TRIAL_TYPES if t[0] == breast_only_code), None)
        assert entry is not None
        _, _, diseases = entry
        assert 'MCL' not in diseases, \
            f'{breast_only_code!r} is BC-specific but contains MCL: {diseases}'


class TestSeededRows:
    """End-state assertions after the migration + LoadTrialTaxonomy run."""

    @pytest.mark.django_db
    def test_every_taxonomy_code_has_a_trial_type_row(self):
        existing_codes = set(TrialType.objects.values_list('code', flat=True))
        expected_codes = {code for code, _, _ in ALL_TRIAL_TYPES}
        missing = expected_codes - existing_codes
        assert not missing, f'TrialType rows missing for taxonomy codes: {sorted(missing)}'

    @pytest.mark.django_db
    def test_mcl_has_exact_expected_trial_type_set(self):
        """Issue #42 headline: MCL's connections match the taxonomy exactly."""
        mcl = Disease.objects.filter(code__iexact='MCL').first()
        assert mcl is not None, 'MCL Disease row not seeded — check LoadMclOptions in conftest'

        actual = set(
            TrialType.objects.filter(diseases=mcl).values_list('code', flat=True)
        )
        expected = {code for code, _, diseases in ALL_TRIAL_TYPES if 'MCL' in diseases}

        missing_from_db = expected - actual
        extra_in_db = actual - expected
        assert not missing_from_db, (
            f'TrialTypeDiseaseConnection missing for MCL: {sorted(missing_from_db)}'
        )
        # `extra_in_db` is informational — the legacy `_seed_trial_types`
        # in seed_reference_data.py is not called from conftest, so the
        # test DB should have zero extras. If it does, surface them.
        assert not extra_in_db, (
            f'Unexpected TrialTypeDiseaseConnection entries for MCL: {sorted(extra_in_db)}'
        )

    @pytest.mark.django_db
    def test_purpose_rows_match_taxonomy_categories(self):
        """`#44`: TrialPurpose codes are seeded from `TRIAL_TAXONOMY.keys()`.
        If a new top-level category is added to the taxonomy, the purpose
        migration must seed a row for it. Reverse also: if a purpose row
        is added by a future migration, the taxonomy must have a category.
        """
        from trials.models import TrialPurpose
        purpose_codes = set(TrialPurpose.objects.values_list('code', flat=True))
        taxonomy_categories = set(TRIAL_TAXONOMY.keys())
        missing = taxonomy_categories - purpose_codes
        extra = purpose_codes - taxonomy_categories
        assert not missing, f'TrialPurpose rows missing for taxonomy categories: {sorted(missing)}'
        assert not extra, f'TrialPurpose rows without a taxonomy category: {sorted(extra)}'

    @pytest.mark.django_db
    def test_mcl_does_not_have_pi3k_inhibitors(self):
        """DB-level regression for issue #42's headline assertion."""
        mcl = Disease.objects.filter(code__iexact='MCL').first()
        assert mcl is not None
        pi3k_exists_for_mcl = (
            TrialType.objects
            .filter(code='targeted_therapy_pi3k_inhibitors', diseases=mcl)
            .exists()
        )
        assert not pi3k_exists_for_mcl, (
            'TrialTypeDiseaseConnection wrongly links MCL to PI3K Inhibitors — '
            'PI3K inhibitors are not approved for MCL per issue #42 spec.'
        )
