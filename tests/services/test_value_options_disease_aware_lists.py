"""Tests for the per-disease option-list helpers (#63 / CB #4330).

Extends the existing `_by_disease_code` pattern (trial_types, therapy_outcomes)
to eight clinically disease-specific lists:

- FLIPI + GELF                 → FL only
- Binet + Richter + diseaseActivity → CLL only
- tumorBurden                  → FL, CLL, MCL (GELF / iwCLL / bulky-disease)
- cytogenicMarkers + molecularMarkers → all hematological (MM/FL/CLL/MCL)

Off-disease patients receive an empty option set (or `{'': '...'}` if the
source list has the '' Unknown sentinel — preserved so the form still
renders a valid blank).
"""
import pytest

from trials.services.value_options import ValueOptions


class TestDiseaseAwareOptionLists:
    @pytest.mark.django_db
    def test_flipi_scores_only_for_fl(self):
        v = ValueOptions()
        full = set(v.flipi_scores.keys())
        assert full  # sanity

        assert set(v.flipi_scores_by_disease_code('FL').keys()) == full
        for off_disease in ('MM', 'BC', 'CLL', 'MCL'):
            # flipi_scores has '' = Unknown sentinel; off-disease preserves it.
            keys = set(v.flipi_scores_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_gelf_criteria_only_for_fl(self):
        v = ValueOptions()
        full = set(v.gelf_criteria_statuses.keys())
        assert full

        assert set(v.gelf_criteria_statuses_by_disease_code('FL').keys()) == full
        for off_disease in ('MM', 'BC', 'CLL', 'MCL'):
            keys = set(v.gelf_criteria_statuses_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_cytogenic_markers_for_hematological_malignancies(self):
        # MM (del(17p13), t(4;14), 1q21amp...), CLL (del(17p), del(11q),
        # trisomy 12), FL (BCL2/BCL6), MCL (t(11;14)). BC uses ER/PR/HER2.
        v = ValueOptions()
        full = set(v.cytogenic_markers.keys())
        assert full

        for on_disease in ('MM', 'FL', 'CLL', 'MCL'):
            assert set(v.cytogenic_markers_by_disease_code(on_disease).keys()) == full, on_disease
        bc_keys = set(v.cytogenic_markers_by_disease_code('BC').keys())
        assert bc_keys == ({''} if '' in full else set())

    @pytest.mark.django_db
    def test_molecular_markers_for_hematological_malignancies(self):
        # MM (TP53, KRAS/NRAS/BRAF), FL (BCL2/EZH2/KMT2D/CREBBP), CLL
        # (TP53/NOTCH1/SF3B1/ATM), MCL (TP53/NOTCH1/CCND1).
        v = ValueOptions()
        full = set(v.molecular_markers.keys())
        assert full

        for on_disease in ('MM', 'FL', 'CLL', 'MCL'):
            assert set(v.molecular_markers_by_disease_code(on_disease).keys()) == full, on_disease
        bc_keys = set(v.molecular_markers_by_disease_code('BC').keys())
        assert bc_keys == ({''} if '' in full else set())

    @pytest.mark.django_db
    def test_binet_stages_only_for_cll(self):
        v = ValueOptions()
        full = set(v.binet_stages.keys())
        assert full

        assert set(v.binet_stages_by_disease_code('CLL').keys()) == full
        for off_disease in ('MM', 'FL', 'BC', 'MCL'):
            keys = set(v.binet_stages_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_richter_transformations_only_for_cll(self):
        v = ValueOptions()
        full = set(v.richter_transformations.keys())
        assert full

        assert set(v.richter_transformations_by_disease_code('CLL').keys()) == full
        for off_disease in ('MM', 'FL', 'BC', 'MCL'):
            keys = set(v.richter_transformations_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_tumor_burdens_for_lymphomas_and_cll(self):
        # GELF criteria → FL; iwCLL burden → CLL; bulky-disease criteria → MCL.
        v = ValueOptions()
        full = set(v.tumor_burdens.keys())
        assert full

        for on_disease in ('FL', 'CLL', 'MCL'):
            assert set(v.tumor_burdens_by_disease_code(on_disease).keys()) == full, on_disease
        for off_disease in ('MM', 'BC'):
            keys = set(v.tumor_burdens_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_disease_activities_only_for_cll(self):
        v = ValueOptions()
        full = set(v.disease_activities.keys())
        assert full

        assert set(v.disease_activities_by_disease_code('CLL').keys()) == full
        for off_disease in ('MM', 'FL', 'BC', 'MCL'):
            keys = set(v.disease_activities_by_disease_code(off_disease).keys())
            assert keys == ({''} if '' in full else set()), off_disease

    @pytest.mark.django_db
    def test_disease_code_is_case_insensitive(self):
        v = ValueOptions()
        assert v.flipi_scores_by_disease_code('fl') == v.flipi_scores_by_disease_code('FL')
        assert v.cytogenic_markers_by_disease_code('mm') == v.cytogenic_markers_by_disease_code('MM')

    @pytest.mark.django_db
    def test_all_options_exposes_per_disease_keys(self):
        all_opts = ValueOptions().all_options()
        for base in ('flipiScore', 'cytogenicMarkers', 'molecularMarkers',
                     'gelfCriteriaStatus', 'binetStages', 'richterTransformations',
                     'tumorBurdens', 'diseaseActivities'):
            for suffix in ('Mm', 'Fl', 'Bc', 'Cll', 'Mcl'):
                key = f'{base}{suffix}'
                assert key in all_opts, key
                assert 'options' in all_opts[key], key
