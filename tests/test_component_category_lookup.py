"""Tests for ComponentCategoryOmopLookup model, sync logic, and management command."""
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import (
    TherapyComponent,
    TherapyComponentCategory,
    TherapyComponentCategoryConnection,
)
from trials.models import TherapyComponentCategoryConnection as _Conn
from trials.services.omop.component_category_lookup import (
    build_component_category_lookup,
    component_concept_ids_to_type_codes,
    component_lookup_request_cache,
    sync_component_category_lookup,
)
from vocab_mirror.models import ComponentCategoryOmopLookup

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


# ── release stamp (#262 / ADR 0002 B′) ───────────────────────────────────────

def test_sync_stamps_explicit_release():
    from vocab_mirror.models import ComponentLookupStamp
    _link(_make_component('zz_stamp1', concept_id=606001), _make_category('zz_stamp_cat'))

    r = sync_component_category_lookup(release_id=42)
    assert ComponentLookupStamp.objects.get().release_id == 42
    assert r['stamped_release'] == 42


def test_sync_defaults_stamp_to_active_release():
    from vocab_mirror.models import ComponentLookupStamp, MirrorRelease
    MirrorRelease.objects.create(release_id=7, state=MirrorRelease.ACTIVE)
    _link(_make_component('zz_stamp2', concept_id=606002), _make_category('zz_stamp_cat2'))

    sync_component_category_lookup()  # release_id=None → active_release_id() == 7
    assert ComponentLookupStamp.objects.get().release_id == 7


def test_sync_without_active_release_writes_no_stamp():
    from vocab_mirror.models import ComponentLookupStamp
    _link(_make_component('zz_stamp3', concept_id=606003), _make_category('zz_stamp_cat3'))

    r = sync_component_category_lookup()  # no active release, none passed
    assert not ComponentLookupStamp.objects.exists()
    assert r['stamped_release'] is None


def test_stamp_is_singleton_latest_wins():
    from vocab_mirror.models import ComponentLookupStamp
    _link(_make_component('zz_stamp4', concept_id=606004), _make_category('zz_stamp_cat4'))

    sync_component_category_lookup(release_id=1)
    sync_component_category_lookup(release_id=2)
    assert ComponentLookupStamp.objects.count() == 1
    assert ComponentLookupStamp.objects.get().release_id == 2


def test_dry_run_writes_no_stamp():
    from vocab_mirror.models import ComponentLookupStamp
    _link(_make_component('zz_stamp5', concept_id=606005), _make_category('zz_stamp_cat5'))

    sync_component_category_lookup(release_id=5, dry_run=True)
    assert not ComponentLookupStamp.objects.exists()


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


# ── request-scoped memo (#266 / ADR 0002 guard #8) ───────────────────────────

def test_request_cache_dedups_repeated_lookups(django_assert_num_queries):
    _link(_make_component('zz_r1', concept_id=770001), _make_category('zz_rcat'))
    sync_component_category_lookup()
    with component_lookup_request_cache():
        with django_assert_num_queries(1):  # second call served from the memo
            a = component_concept_ids_to_type_codes(['770001'])
            b = component_concept_ids_to_type_codes(['770001'])
    assert a == b == ['zz_rcat']


def test_no_memo_outside_request_context(django_assert_num_queries):
    _link(_make_component('zz_r3', concept_id=770003), _make_category('zz_r3cat'))
    sync_component_category_lookup()
    # No request cache active → each call reads the table (no cross-call memo).
    with django_assert_num_queries(2):
        component_concept_ids_to_type_codes(['770003'])
        component_concept_ids_to_type_codes(['770003'])


def test_memo_does_not_persist_across_requests():
    # A rebuild between two request contexts: the second context must see the new
    # data, never a stale memo. (This is a same-process behavior guard; the actual
    # #266 bug was cross-PROCESS — a sync job's flush never reaching a separate web
    # worker — which a single-process test can't exercise. See the PR for that.)
    comp = _make_component('zz_r2', concept_id=770002)
    _link(comp, _make_category('zz_before'))
    sync_component_category_lookup()
    with component_lookup_request_cache():
        assert component_concept_ids_to_type_codes(['770002']) == ['zz_before']
    # rebuild the mapping (as the sync job would, out of band)
    _Conn.objects.all().delete()
    _link(comp, _make_category('zz_after'))
    sync_component_category_lookup()
    with component_lookup_request_cache():
        assert component_concept_ids_to_type_codes(['770002']) == ['zz_after']


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
