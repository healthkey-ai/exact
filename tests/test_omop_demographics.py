"""OMOP demographics (ethnicity/gender) conversion + backfill + seam (epic #4447).

Ported from CancerBot. The profile/config stay on the LEGACY columns (behavior-
preserving); this covers the infrastructure (mapper, loader, backfill, shadow-compare,
the demographics seam) that the eventual cutover flips.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import Trial, Ethnicity
from trials.services.omop.demographics import build_omop_demographics
from trials.services.omop.demographics_match_profile import DEMOGRAPHICS_MATCH_PROFILE
from tests.factories import TrialFactory


@pytest.fixture
def loaded_ethnicity(db):
    # conftest seeds the Ethnicity options; load their omop_concept_ids
    call_command('load_ethnicity_omop_concept_ids', stdout=StringIO())


@pytest.mark.django_db
class TestBuildOmopDemographics:
    def test_maps_ethnicity_and_gender(self, loaded_ethnicity):
        trial = TrialFactory(
            ethnicity_required=['caucasian_or_european', 'asian', 'other'],
            gender='M',
        )
        values, unmapped = build_omop_demographics(trial)
        assert values['omop_ethnicity_required'] == ['45877987', '45879439']  # White, Asian sorted
        assert values['omop_gender_concept_id'] == 45880669  # Male
        assert unmapped == ['other']  # no clean concept

    def test_empty_inputs(self, loaded_ethnicity):
        trial = TrialFactory(ethnicity_required=[], gender='')
        values, unmapped = build_omop_demographics(trial)
        assert values == {'omop_ethnicity_required': [], 'omop_gender_concept_id': None}
        assert unmapped == []

    def test_female_gender(self, loaded_ethnicity):
        trial = TrialFactory(ethnicity_required=[], gender='F')
        values, _ = build_omop_demographics(trial)
        assert values['omop_gender_concept_id'] == 45878463  # Female


@pytest.mark.django_db
class TestEthnicityLoader:
    def test_loader_sets_omop_concept_id(self, loaded_ethnicity):
        assert Ethnicity.objects.get(code='asian').omop_concept_id == 45879439
        assert Ethnicity.objects.get(code='caucasian_or_european').omop_concept_id == 45877987
        # 'other' is intentionally unmapped
        other = Ethnicity.objects.filter(code='other').first()
        if other is not None:
            assert other.omop_concept_id is None

    def test_loader_clears_stale_concept_id_for_uncurated_code(self, db):
        # a code outside the curated map carrying a stale value must be cleared,
        # so build_omop_demographics never treats it as mapped.
        other, _ = Ethnicity.objects.get_or_create(code='other', defaults={'title': 'Other'})
        Ethnicity.objects.filter(pk=other.pk).update(omop_concept_id=99999999)
        out = StringIO()
        call_command('load_ethnicity_omop_concept_ids', stdout=out)
        other.refresh_from_db()
        assert other.omop_concept_id is None
        assert 'cleared=1 ' in out.getvalue()

    def test_loader_corrects_mapped_code_and_does_not_clear_it(self, db):
        # a MAPPED code carrying a wrong value must be corrected to the curated
        # cid by the set loop, NOT nulled by the clear step (disjointness).
        asian, _ = Ethnicity.objects.get_or_create(code='asian', defaults={'title': 'Asian'})
        Ethnicity.objects.filter(pk=asian.pk).update(omop_concept_id=11111111)
        call_command('load_ethnicity_omop_concept_ids', stdout=StringIO())
        asian.refresh_from_db()
        assert asian.omop_concept_id == 45879439


@pytest.mark.django_db
class TestBackfillCommand:
    def test_backfill_populates(self, loaded_ethnicity):
        trial = TrialFactory(ethnicity_required=['african_or_black'], gender='F')
        call_command('backfill_omop_demographics_columns', stdout=StringIO())
        trial.refresh_from_db()
        assert trial.omop_ethnicity_required == ['45877988']
        assert trial.omop_gender_concept_id == 45878463

    def test_dry_run_does_not_write(self, loaded_ethnicity):
        trial = TrialFactory(ethnicity_required=['asian'], gender='M')
        out = StringIO()
        call_command('backfill_omop_demographics_columns', '--dry-run', stdout=out)
        trial.refresh_from_db()
        assert trial.omop_ethnicity_required == []
        assert trial.omop_gender_concept_id is None
        assert 'dry-run' in out.getvalue()

    def test_idempotent(self, loaded_ethnicity):
        TrialFactory(ethnicity_required=['asian'], gender='M')
        call_command('backfill_omop_demographics_columns', stdout=StringIO())
        out = StringIO()
        call_command('backfill_omop_demographics_columns', stdout=out)
        assert 'updated 0' in out.getvalue()


class TestDemographicsMatchProfile:
    def test_profile_maps_to_legacy_column_names(self):
        # guard: keep pointing at legacy columns until the deliberate OMOP cutover
        assert DEMOGRAPHICS_MATCH_PROFILE.ethnicity_required == 'ethnicity_required'
        assert DEMOGRAPHICS_MATCH_PROFILE.gender == 'gender'

    def test_omop_ethnicity_gin_index_present(self):
        names = {i.name for i in Trial._meta.indexes}
        assert 'idx_omop_ethnicity_req_gin' in names


@pytest.mark.django_db
def test_eligible_for_ethnicity_behavior_preserved():
    # eligible_for_required_lists: include when required overlaps the patient
    # values OR required is empty. Reads the column via DEMOGRAPHICS_MATCH_PROFILE
    # (still the legacy name) — behavior-preserving.
    t_any = TrialFactory(ethnicity_required=[])
    t_white = TrialFactory(ethnicity_required=['caucasian_or_european'])
    t_asian = TrialFactory(ethnicity_required=['asian'])

    total = Trial.objects.count()
    assert Trial.objects.eligible_for_ethnicity(None).count() == total
    assert Trial.objects.eligible_for_ethnicity([]).count() == total

    assert set(Trial.objects.eligible_for_ethnicity(['caucasian_or_european'])) == {t_any, t_white}
    assert set(Trial.objects.eligible_for_ethnicity(['asian'])) == {t_any, t_asian}
    assert set(Trial.objects.eligible_for_ethnicity(['zzz'])) == {t_any}  # only empty-required passes


@pytest.mark.django_db
def test_shadow_compare_covers_demographics(loaded_ethnicity):
    from trials.services.omop.shadow_compare import compare_trial
    # legacy demographics set, omop columns stale (empty defaults) -> drift on both;
    # 'other' has no concept -> reported unmapped.
    trial = TrialFactory(ethnicity_required=['asian', 'other'], gender='F')
    drift, unmapped = compare_trial(trial)
    assert 'omop_ethnicity_required' in drift
    assert 'omop_gender_concept_id' in drift
    assert unmapped.get('ethnicity_required') == ['other']
