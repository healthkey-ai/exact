"""Tests for ComponentCategoryOmopLookup model, sync logic, and management command."""
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import (
    ComponentCategoryOmopLookup,
    TherapyComponent,
    TherapyComponentCategory,
    TherapyComponentCategoryConnection,
)
from trials.services.omop.component_category_lookup import (
    build_component_category_lookup,
    component_concept_ids_to_type_codes,
    sync_component_category_lookup,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_lookup_lru_cache():
    """Clear the module-level LRU cache before each test to prevent cross-test pollution."""
    from trials.services.omop.component_category_lookup import clear_lookup_cache
    clear_lookup_cache()
    yield
    clear_lookup_cache()


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_component(code, concept_id=None):
    return TherapyComponent.objects.create(code=code, title=code, omop_concept_id=concept_id)


def _make_category(code):
    return TherapyComponentCategory.objects.create(code=code, title=code)


def _link(component, category):
    TherapyComponentCategoryConnection.objects.create(component=component, category=category)


# ── build_component_category_lookup ──────────────────────────────────────────

def test_build_lookup_basic():
    comp = _make_component('zz_bort', concept_id=1336825)
    cat = _make_category('zz_pi')
    _link(comp, cat)

    result = build_component_category_lookup()
    assert result[1336825] == ['zz_pi']


def test_build_lookup_skips_null_omop_concept_id():
    comp = _make_component('zz_noomop', concept_id=None)
    cat = _make_category('zz_pi2')
    _link(comp, cat)

    result = build_component_category_lookup()
    assert not any(codes for codes in result.values() if 'zz_pi2' in codes)


def test_build_lookup_merges_shared_concept_id():
    """Two components with the same omop_concept_id → category codes union."""
    comp_a = _make_component('zz_comp_a', concept_id=999001)
    comp_b = _make_component('zz_comp_b', concept_id=999001)
    cat1 = _make_category('zz_cat1')
    cat2 = _make_category('zz_cat2')
    _link(comp_a, cat1)
    _link(comp_b, cat2)

    result = build_component_category_lookup()
    assert result[999001] == ['zz_cat1', 'zz_cat2']


def test_build_lookup_deduplicates_category_codes():
    """Same concept_id, same category via two components → code appears once."""
    comp_a = _make_component('zz_dup_a', concept_id=999002)
    comp_b = _make_component('zz_dup_b', concept_id=999002)
    cat = _make_category('zz_same_cat')
    _link(comp_a, cat)
    _link(comp_b, cat)

    result = build_component_category_lookup()
    assert result[999002] == ['zz_same_cat']


# ── sync_component_category_lookup ───────────────────────────────────────────

def test_sync_adds_new_rows():
    comp = _make_component('zz_len', concept_id=1313346)
    cat = _make_category('zz_imid')
    _link(comp, cat)

    r = sync_component_category_lookup()
    assert ComponentCategoryOmopLookup.objects.filter(component_concept_id=1313346).exists()
    assert r['added'] >= 1


def test_sync_updates_changed_row():
    ComponentCategoryOmopLookup.objects.create(component_concept_id=888001, category_codes=['zz_old'])
    comp = _make_component('zz_comp_upd', concept_id=888001)
    cat = _make_category('zz_new')
    _link(comp, cat)

    r = sync_component_category_lookup()
    row = ComponentCategoryOmopLookup.objects.get(component_concept_id=888001)
    assert row.category_codes == ['zz_new']
    assert r['updated'] >= 1


def test_sync_removes_stale_rows():
    ComponentCategoryOmopLookup.objects.create(component_concept_id=777001, category_codes=['zz_stale'])

    r = sync_component_category_lookup()
    assert not ComponentCategoryOmopLookup.objects.filter(component_concept_id=777001).exists()
    assert r['removed'] >= 1


def test_sync_unchanged_rows_not_touched():
    comp = _make_component('zz_stable', concept_id=666001)
    cat = _make_category('zz_stable_cat')
    _link(comp, cat)
    ComponentCategoryOmopLookup.objects.create(component_concept_id=666001, category_codes=['zz_stable_cat'])

    r = sync_component_category_lookup()
    assert r['unchanged'] >= 1


def test_sync_dry_run_does_not_write():
    comp = _make_component('zz_dry', concept_id=555001)
    cat = _make_category('zz_dry_cat')
    _link(comp, cat)

    r = sync_component_category_lookup(dry_run=True)
    assert not ComponentCategoryOmopLookup.objects.filter(component_concept_id=555001).exists()
    assert r['added'] >= 1  # drift reported, not applied


def test_sync_is_idempotent():
    comp = _make_component('zz_idem', concept_id=444001)
    cat = _make_category('zz_idem_cat')
    _link(comp, cat)

    sync_component_category_lookup()
    r2 = sync_component_category_lookup()
    assert r2['added'] == 0
    assert r2['updated'] == 0
    assert r2['removed'] == 0


# ── component_concept_ids_to_type_codes ──────────────────────────────────────

def test_consumer_api_returns_none_for_none():
    assert component_concept_ids_to_type_codes(None) is None


def test_consumer_api_returns_empty_for_empty_list():
    assert component_concept_ids_to_type_codes([]) == []


def test_consumer_api_resolves_via_table():
    ComponentCategoryOmopLookup.objects.create(component_concept_id=1336825, category_codes=['pi'])
    result = component_concept_ids_to_type_codes(['1336825'])
    assert 'pi' in result


def test_consumer_api_returns_empty_for_unknown_concept_id():
    result = component_concept_ids_to_type_codes(['9999999'])
    assert result == []


# ── management command ────────────────────────────────────────────────────────

def test_command_populates_table():
    comp = _make_component('zz_cmd', concept_id=333001)
    cat = _make_category('zz_cmd_cat')
    _link(comp, cat)

    call_command('rebuild_component_category_omop_lookup', stdout=StringIO())
    assert ComponentCategoryOmopLookup.objects.filter(component_concept_id=333001).exists()


def test_command_dry_run_reports_drift_without_writing():
    comp = _make_component('zz_cmd_dry', concept_id=222001)
    cat = _make_category('zz_cmd_dry_cat')
    _link(comp, cat)

    out = StringIO()
    call_command('rebuild_component_category_omop_lookup', dry_run=True, stdout=out)
    output = out.getvalue()
    assert 'added' in output
    assert not ComponentCategoryOmopLookup.objects.filter(component_concept_id=222001).exists()
