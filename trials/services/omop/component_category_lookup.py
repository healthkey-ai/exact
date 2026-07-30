"""Component concept_id → CB category codes lookup for OMOP type matching (#4503).

Two responsibilities:

1. **Consumer API** — ``component_concept_ids_to_type_codes(concept_ids)``: maps the
   patient's component concept_ids (RxNorm ingredients) to CB category codes (CB
   hierarchy, not OMOP — EXACT ADR 0001 decision A / #4502).  Reads from the flat
   :class:`~vocab_mirror.models.ComponentCategoryOmopLookup` table; no graph traversal.

2. **Maintenance** — ``sync_component_category_lookup()``: reconciles the flat table
   from the local M2M graph (TherapyComponent → TherapyComponentCategory).  Run after
   ``load_therapy_omop_concept_ids`` or any vocab change that bypasses post_save.
   Ported from CancerBot (CB owns the upstream; EXACT rebuilds from its local copy).
"""
import contextvars

from django.db import transaction

from trials.models import TherapyComponentCategoryConnection
from vocab_mirror.activation import active_release_id
from vocab_mirror.models import ComponentCategoryOmopLookup, ComponentLookupStamp


# ── consumer API ────────────────────────────────────────────────────────────

# Request-scoped memo. The matcher calls the lookup once PER TRIAL scored with the
# SAME patient component set, so within a request we dedup those to one query. It
# is deliberately NOT a process-global cache: the table is rebuilt by a separate
# sync job, and a process-global cache would leave web workers serving stale
# type-codes until restart (ADR 0002 guard #8, #266). Scoped to a request → a
# rebuild between requests is always picked up; outside a request (backfill/sync)
# the memo is unset and every call reads the table directly.
_request_memo: contextvars.ContextVar = contextvars.ContextVar(
    'component_lookup_request_memo', default=None)


def component_concept_ids_to_type_codes(concept_ids):
    """Patient component concept_ids → list of CB category codes for type matching.

    Returns ``None`` when ``concept_ids`` is ``None`` (unknown → preserve
    unknown semantics in the matcher). Returns ``[]`` when the lookup yields
    nothing (known-empty → no types to match).

    Input order is ignored. Inside a :class:`component_lookup_request_cache`
    block the same concept_id set is resolved once and reused for the request.
    """
    if concept_ids is None:
        return None
    key = tuple(sorted(concept_ids, key=str))
    memo = _request_memo.get()
    if memo is None:
        return _lookup(key)
    if key not in memo:
        memo[key] = _lookup(key)
    return memo[key]


def _lookup(concept_ids_tuple):
    """Read CB category codes for a set of component concept_ids (single query)."""
    if not concept_ids_tuple:
        return []
    cids = [int(v) for v in concept_ids_tuple if str(v).isdigit()]
    if not cids:
        return []
    codes: set[str] = set()
    for row in ComponentCategoryOmopLookup.objects.filter(component_concept_id__in=cids):
        codes.update(row.category_codes)
    return sorted(codes)


class component_lookup_request_cache:
    """Enable the request-scoped lookup memo for a ``with`` block.

    Enter once per request (the trials view does, in ``dispatch``) so repeated
    per-trial lookups dedup to one query; the memo resets on exit, so nothing
    persists across requests or workers.
    """

    def __enter__(self):
        self._token = _request_memo.set({})
        return self

    def __exit__(self, *exc):
        _request_memo.reset(self._token)
        return False


def clear_lookup_cache():
    """Clear the request-scoped memo if one is active.

    There is no longer a process-global cache to flush across workers — the memo
    is request-scoped (#266), so a table rebuild in the sync job can never strand
    stale entries in a web worker. Retained (a) so the rebuild path and existing
    callers/tests need no change, and (b) to drop any memo within the current
    request after an in-request rebuild.
    """
    memo = _request_memo.get()
    if memo is not None:
        memo.clear()


# ── maintenance (rebuild from local M2M graph) ──────────────────────────────

def build_component_category_lookup():
    """Compute ``{component_concept_id: [category_code, ...]}`` from the M2M graph.

    Connections with a null component, a component whose ``omop_concept_id`` is
    null, or a null category are skipped. Category codes per concept_id are
    de-duplicated and sorted; a concept_id shared by multiple components unions
    their category codes.
    """
    pairs = (
        TherapyComponentCategoryConnection.objects
        .filter(component__omop_concept_id__isnull=False, category__isnull=False)
        .values_list('component__omop_concept_id', 'category__code')
    )
    lookup: dict[int, set[str]] = {}
    for concept_id, code in pairs:
        if not code:
            continue
        lookup.setdefault(concept_id, set()).add(code)
    return {cid: sorted(codes) for cid, codes in lookup.items()}


def sync_component_category_lookup(release_id=None, dry_run=False):
    """Reconcile :class:`ComponentCategoryOmopLookup` and stamp it to a release.

    Full-table reconcile (the component vocab is small): upsert new/changed rows,
    delete stale ones, and — in the **same transaction** — write the
    :class:`ComponentLookupStamp` recording the mirror ``release_id`` this payload
    was validated against, so a reader never sees a payload/stamp mismatch (#262 /
    ADR 0002 B′). ``release_id`` defaults to the currently active mirror release
    (``active_release_id()``); pass it explicitly from the sync flow when
    publishing for a not-yet-active generation. If neither yields a release (no
    active generation, none passed) the payload is still reconciled but no stamp is
    written — there is no release to bind it to yet.

    Returns ``{'added','updated','removed','unchanged','total','stamped_release'}``.
    With ``dry_run=True`` nothing is written and counts reflect drift.
    """
    if release_id is None:
        release_id = active_release_id()
    with transaction.atomic(using='default'):
        computed = build_component_category_lookup()
        existing = {
            row.component_concept_id: row.category_codes
            for row in ComponentCategoryOmopLookup.objects.all()
        }

        added = updated = unchanged = 0
        for cid, codes in computed.items():
            if cid not in existing:
                added += 1
                if not dry_run:
                    ComponentCategoryOmopLookup.objects.create(
                        component_concept_id=cid, category_codes=codes,
                    )
            elif sorted(existing[cid] or []) != codes:
                updated += 1
                if not dry_run:
                    ComponentCategoryOmopLookup.objects.filter(
                        component_concept_id=cid,
                    ).update(category_codes=codes)
            else:
                unchanged += 1

        stale = [cid for cid in existing if cid not in computed]
        if stale and not dry_run:
            ComponentCategoryOmopLookup.objects.filter(component_concept_id__in=stale).delete()

        # Stamp the freshly published payload to the release, in the same txn.
        if not dry_run and release_id is not None:
            ComponentLookupStamp.objects.update_or_create(
                singleton=True, defaults={'release_id': release_id},
            )

    if not dry_run:
        # Flush the in-process LRU cache so subsequent calls see the updated table.
        clear_lookup_cache()

    return {
        'added': added,
        'updated': updated,
        'removed': len(stale),
        'unchanged': unchanged,
        'total': len(computed),
        'stamped_release': release_id if not dry_run else None,
    }
