"""cb<->OMOP therapy crosswalk table (#4476). Ported from CancerBot."""
import pytest
from django.db import IntegrityError

from trials.models import TherapyOmopMapping


@pytest.mark.django_db
def test_unique_per_level_and_cb_code():
    TherapyOmopMapping.objects.create(level='regimen', cb_code='vrd', match='auto', omop_concept_id=1)
    with pytest.raises(IntegrityError):
        TherapyOmopMapping.objects.create(level='regimen', cb_code='vrd', match='curated')


@pytest.mark.django_db
def test_same_cb_code_allowed_across_levels():
    # cb_code is only unique within a level — the same string can be a regimen
    # and a component, mapping to different concepts.
    TherapyOmopMapping.objects.create(level='regimen', cb_code='dup', match='auto', omop_concept_id=1)
    TherapyOmopMapping.objects.create(level='component', cb_code='dup', match='auto', omop_concept_id=2)
    assert TherapyOmopMapping.objects.filter(cb_code='dup').count() == 2


@pytest.mark.django_db
def test_unmapped_row_allows_null_concept():
    row = TherapyOmopMapping.objects.create(level='regimen', cb_code='asct', match='no_omop')
    assert row.omop_concept_id is None
    assert row.omop_name is None and row.omop_vocab is None
