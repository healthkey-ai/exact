"""
OMOP cutover phase 2: read-only omop_* columns exist and round-trip.

EXACT reads CB's omop_* trial columns + the omop_concept_id source columns on the
therapy taxonomy. Concept_ids are stored as STRINGS in JSONB on the trial columns
(so has_any_keys/GIN overlap carries over) and as BigIntegers on the source rows.
"""
import pytest

from trials.models import Trial, Therapy, TherapyComponent, TherapyComponentCategory
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def test_trial_omop_columns_round_trip_string_concept_ids():
    t = TrialFactory(
        omop_therapies_required=['12345', '67890'],
        omop_therapy_types_excluded=['111'],
        omop_therapy_components_required=['222'],
        omop_supportive_therapies_required=['333'],
        omop_planned_therapies_excluded=['444'],
    )
    t.refresh_from_db()
    assert t.omop_therapies_required == ['12345', '67890']
    assert t.omop_therapy_types_excluded == ['111']
    assert t.omop_therapy_components_required == ['222']
    assert t.omop_supportive_therapies_required == ['333']
    assert t.omop_planned_therapies_excluded == ['444']


def test_trial_omop_columns_support_has_any_keys_overlap():
    # The load-bearing reason concept_ids are stored as strings: matching uses
    # JSONB has_any_keys (?|), which keys on string array elements. This is the
    # exact filter the matcher will run once flipped to the omop columns.
    t = TrialFactory(omop_therapies_required=['12345', '67890'])
    assert Trial.objects.filter(omop_therapies_required__has_any_keys=['12345']).filter(pk=t.pk).exists()
    assert not Trial.objects.filter(omop_therapies_required__has_any_keys=['99999']).filter(pk=t.pk).exists()


def test_trial_omop_columns_default_empty_list():
    t = TrialFactory()
    for f in (
        'omop_therapies_required', 'omop_therapies_excluded',
        'omop_therapy_types_required', 'omop_therapy_types_excluded',
        'omop_therapy_components_required', 'omop_therapy_components_excluded',
        'omop_supportive_therapies_required', 'omop_supportive_therapies_excluded',
        'omop_planned_therapies_required', 'omop_planned_therapies_excluded',
    ):
        assert getattr(t, f) == [], f


def test_taxonomy_omop_concept_id_source_columns():
    th = Therapy.objects.create(code='zz_omop_t', title='ZZ omop t', omop_concept_id=12345)
    comp = TherapyComponent.objects.create(code='zz_omop_c', title='ZZ omop c', omop_concept_id=222)
    cat = TherapyComponentCategory.objects.create(code='zz_omop_cat', title='ZZ omop cat', omop_concept_id=111)
    th.refresh_from_db(); comp.refresh_from_db(); cat.refresh_from_db()
    assert th.omop_concept_id == 12345
    assert comp.omop_concept_id == 222
    assert cat.omop_concept_id == 111
    # Source columns are nullable (unmapped codes).
    assert Therapy.objects.create(code='zz_omop_t2', title='ZZ omop t2').omop_concept_id is None
