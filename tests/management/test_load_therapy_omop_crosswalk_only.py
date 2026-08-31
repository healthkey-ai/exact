"""crosswalk_only / deferred mapping-status seam (#4580). Ported from CancerBot.

A ``crosswalk_only`` (category / drug-class type) or ``deferred`` (component code not
yet authored) row carries an ``omop_concept_id`` in the CSV for AUDIT only — it must be
recorded in the ``TherapyOmopMapping`` crosswalk but must NEVER become a runtime vocab
mapping (no ``OmopConcept`` title, no vocab ``omop_concept_id``), and any stale vocab
concept_id for the code must be cleared even though the CSV still carries one.

EXACT divergence vs CB: EXACT's ``LEVEL_MODEL`` includes ``category``
(``TherapyComponentCategory``), so the clear rule is load-bearing at the category level
here — a category ``crosswalk_only`` row with a concept_id would otherwise be backfilled
into trial ``omop_*`` columns.
"""
import csv
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import (
    TherapyComponent, TherapyComponentCategory, OmopConcept, TherapyOmopMapping,
)

HEADER = ['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match']


def _write_csv(path, rows):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return str(path)


def _load(csv_path, *extra):
    call_command('load_therapy_omop_concept_ids', '--csv', csv_path, *extra, stdout=StringIO())


@pytest.mark.django_db
def test_crosswalk_only_records_crosswalk_but_no_title_or_vocab(tmp_path):
    # A category row carries a concept_id for audit but is NOT runtime-applicable (#4580):
    # recorded in the crosswalk with match='crosswalk_only', no OmopConcept title, and no
    # vocab write. category is NOT a vocab level (#4502: dropped from LEVEL_MODEL), so the
    # row is recorded for audit only and never touches a vocab omop_concept_id.
    TherapyComponentCategory.objects.create(code='zz_cat', title='ZZ Cat')
    csv_path = _write_csv(tmp_path / 'm.csv', [
        ['category', 'zz_cat', 'ZZ Cat', '21602723', 'Corticosteroids', 'ATC', 'crosswalk_only'],
    ])
    _load(csv_path)

    row = TherapyOmopMapping.objects.get(level='category', cb_code='zz_cat')
    assert row.match == 'crosswalk_only' and row.omop_concept_id == 21602723   # audit preserved
    assert not OmopConcept.objects.filter(concept_id=21602723).exists()        # no runtime title
    assert TherapyComponentCategory.objects.get(code='zz_cat').omop_concept_id is None


@pytest.mark.django_db
def test_deferred_clears_stale_component_cid(tmp_path):
    # A level that IS in LEVEL_MODEL previously had a concept_id; a deferred row must
    # clear it even though the CSV still carries a cid.
    TherapyComponent.objects.create(code='zz_dc', title='ZZ DC', omop_concept_id=99)
    csv_path = _write_csv(tmp_path / 'm.csv', [
        ['component', 'zz_dc', 'ZZ DC', '99', 'ZZ DC', 'RxNorm', 'deferred'],
    ])
    _load(csv_path)

    assert TherapyComponent.objects.get(code='zz_dc').omop_concept_id is None   # cleared
    assert not OmopConcept.objects.filter(concept_id=99).exists()
    assert TherapyOmopMapping.objects.get(level='component', cb_code='zz_dc').match == 'deferred'


@pytest.mark.django_db
def test_crosswalk_only_clears_stale_component_cid(tmp_path):
    # The literal 'crosswalk_only' value (not just 'deferred') clears at a LEVEL_MODEL level.
    TherapyComponent.objects.create(code='zz_co', title='ZZ CO', omop_concept_id=55)
    csv_path = _write_csv(tmp_path / 'm.csv', [
        ['component', 'zz_co', 'ZZ CO', '55', 'ZZ CO', 'RxNorm', 'crosswalk_only'],
    ])
    _load(csv_path)

    assert TherapyComponent.objects.get(code='zz_co').omop_concept_id is None   # cleared


@pytest.mark.django_db
def test_exclude_llm_does_not_clear_existing_llm_vocab(tmp_path):
    # Guard the narrow clear rule: an llm row skipped under --exclude-llm must NOT clear an
    # existing vocab concept_id (only crosswalk_only/deferred/no-concept rows clear).
    TherapyComponent.objects.create(code='zz_llm', title='ZZ LLM', omop_concept_id=88)
    csv_path = _write_csv(tmp_path / 'm.csv', [
        ['component', 'zz_llm', 'ZZ LLM', '88', 'ZZ LLM', 'RxNorm', 'llm'],
    ])
    _load(csv_path, '--exclude-llm')

    assert TherapyComponent.objects.get(code='zz_llm').omop_concept_id == 88    # preserved, not cleared


@pytest.mark.django_db
def test_accepted_row_is_not_cleared_by_the_new_rule(tmp_path):
    # Regression guard: the crosswalk_only/deferred clear branch must never touch an
    # accepted (auto/curated/llm) mapping — a curated row keeps its vocab concept_id.
    TherapyComponent.objects.create(code='zz_keep', title='ZZ Keep', omop_concept_id=77)
    csv_path = _write_csv(tmp_path / 'm.csv', [
        ['component', 'zz_keep', 'ZZ Keep', '77', 'ZZ Keep', 'RxNorm', 'curated'],
    ])
    _load(csv_path)

    assert TherapyComponent.objects.get(code='zz_keep').omop_concept_id == 77   # preserved
