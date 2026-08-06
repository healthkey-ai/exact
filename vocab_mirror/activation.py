"""Release contract + atomic activation for the OMOP vocabulary mirror (#249).

The mirror data tables (``MirrorConcept`` &c.) are generation-tagged by
``release_id`` and loaded off-line (#250). This module is the **control plane**:
which generation is live, and the atomic swap to a new one.

Guarantees (ADR 0002):
- **At most one ACTIVE release** — enforced both by the DB (a partial-unique
  index on ``state='active'``) and by :func:`activate_release`, which does the
  demote-old + promote-new in one ``default``-DB transaction. A reader that binds
  :func:`active_release_id` at request start therefore pins one coherent
  generation for its whole run; a concurrent swap only supersedes the previous
  generation (its rows are retained), it never mutates the pinned one.
- **Fail closed** — :func:`active_release_id` returns ``None`` when nothing is
  active; callers must treat that as "vocabulary unavailable" (503), never fall
  back to a live API or a different release.
- **Release-match gate** — a release is activated only when it is READY and
  every registered cross-artifact check agrees on the release id (see
  :func:`register_release_match_check`). Ships the mirror-side check (the
  generation actually has data) and the component-category lookup cross-artifact
  check (#262: the lookup is stamped for this release and every lookup concept_id
  exists in it). The trial ``omop_*`` projection check (#265) reads CB's local
  **attestation** (published into this ``default`` DB — never a racy cross-DB read
  of the CB trials table); it runs **observe-only** until Phase-T (#263) puts the
  graph on the eligibility path, then enforces (see ``_PROJECTION_GATE_ENFORCE``).
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from vocab_mirror.models import (
    ComponentCategoryOmopLookup,
    ComponentLookupStamp,
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)

logger = logging.getLogger(__name__)

_ACTIVATION_DB = 'default'  # the mirror lives on `default` (see db_router / ADR §Placement)


class MirrorReleaseError(Exception):
    """Base class for activation failures."""


class ReleaseNotReady(MirrorReleaseError):
    """The release does not exist or is not in a state that can be activated."""


class ReleaseMatchFailed(MirrorReleaseError):
    """A cross-artifact release-match check rejected the activation."""


class ConcurrentActivation(MirrorReleaseError):
    """Two activations raced and this one lost at the one-active constraint.

    The single-ACTIVE invariant still held (the DB rejected the second writer);
    this is the typed, retryable signal for the loser, kept inside the
    ``MirrorReleaseError`` contract so callers don't see a raw ``IntegrityError``.
    Activation is expected to run under the #250 sync singleton (advisory lock),
    so this should not occur in normal operation.
    """


# Registry of cross-artifact release-match checks. Each callable takes the
# release_id and raises ``ReleaseMatchFailed`` if its artifact does not agree on
# that release. Kept as a registry (not hard-coded) so #252 / Phase-T can plug in
# the crosswalk / component-lookup / trial-projection checks without touching the
# activation core. v1 seeds only the mirror-side check below.
_release_match_checks = []


def register_release_match_check(func):
    """Register a ``func(release_id)`` that raises ``ReleaseMatchFailed`` unless
    its artifact is consistent with ``release_id``. Returns ``func`` (usable as a
    decorator). Idempotent — registering the same callable twice is a no-op."""
    if func not in _release_match_checks:
        _release_match_checks.append(func)
    return func


# Every mirror table EXACT syncs must be populated for a generation to be
# activatable — otherwise a partial (e.g. concept-only) load would fail *open*:
# traversal over an empty relationship/ancestor table silently returns nothing.
_REQUIRED_MIRROR_MODELS = (
    MirrorVocabulary, MirrorConcept, MirrorConceptRelationship, MirrorConceptAncestor,
)


@register_release_match_check
def _mirror_generation_populated(release_id):
    """Mirror-side gate: refuse to activate a generation that is missing rows in
    any required mirror table (an incomplete/partial load must not go live)."""
    for model in _REQUIRED_MIRROR_MODELS:
        if not model.objects.using(_ACTIVATION_DB).filter(release_id=release_id).exists():
            raise ReleaseMatchFailed(
                f'mirror release {release_id} has no rows in {model.__name__}; '
                'refusing to activate an incomplete generation')


@register_release_match_check
def _component_lookup_matches_release(release_id):
    """Cross-artifact gate for the component→category lookup (#262 / ADR 0002 B′).

    The lookup content is CB-authored and release-independent, but its keys are
    concept_ids that must stay valid against the vocabulary. Refuse to activate a
    release R unless BOTH hold:

    - **Provenance** — the lookup is stamped for R (proof its payload was rebuilt +
      validated for this release, not left stale from an older one). A missing
      stamp is treated as "matching" *only* when the lookup is empty (a fresh
      deploy that has not populated the lookup yet); a populated-but-unstamped
      lookup fails closed.
    - **Referential coverage** — every ``component_concept_id`` in the lookup
      exists as a concept in R's mirror. A component that vanished from R would
      silently stop resolving its type, so we block activation instead.
    """
    lookup_ids = set(
        ComponentCategoryOmopLookup.objects.using(_ACTIVATION_DB)
        .values_list('component_concept_id', flat=True))
    if not lookup_ids:
        # Nothing to validate/pair yet (e.g. lookup not populated on a fresh
        # deploy). Don't block the first activation on an empty derived artifact.
        return

    stamp = (ComponentLookupStamp.objects.using(_ACTIVATION_DB)
             .values_list('release_id', flat=True).first())
    if stamp != release_id:
        raise ReleaseMatchFailed(
            f'component-category lookup is stamped for release {stamp}, not {release_id}; '
            'rebuild the lookup for this release before activating')

    present = set(
        MirrorConcept.objects.using(_ACTIVATION_DB)
        .filter(release_id=release_id, concept_id__in=lookup_ids)
        .values_list('concept_id', flat=True))
    missing = lookup_ids - present
    if missing:
        sample = sorted(missing)[:10]
        raise ReleaseMatchFailed(
            f'component-category lookup references {len(missing)} concept_id(s) absent '
            f'from release {release_id} mirror concepts (e.g. {sample}); '
            'refusing to activate an inconsistent generation')


# Enforce the projection gate only once the mirror graph is on the eligibility path
# (Phase-T, #263). Until then eligibility matches the materialized omop_* columns
# directly (no mirror read), so a projection/mirror release mismatch cannot yet
# affect a verdict — the check runs OBSERVE-ONLY (logs, never blocks activation).
#
# ORDERING (do not flip early): a REMOTE CB cannot run EXACT's management command,
# so in prod the only way an attestation gets published is the HTTP publish endpoint
# (a #265 follow-up). Flipping this to True before that endpoint exists would
# fail-close EVERY activation permanently (CB could never attest). Land the endpoint
# first, confirm CB publishes, then enforce with Phase-T.
_PROJECTION_GATE_ENFORCE = False


@register_release_match_check
def _projection_matches_release(release_id):
    """Cross-artifact gate for the trial ``omop_*`` projection (#265 / ADR 0002 #4).

    The projection is CB-owned on the trials DB; rather than a racy cross-DB read,
    the gate checks for CB's **attestation** (published into EXACT's ``default`` DB
    once CB has validated the projection for R). Missing attestation → the projection
    is not known-consistent with R. **Observe-only until Phase-T**: log it and
    activate anyway (eligibility doesn't read the mirror yet); flip
    ``_PROJECTION_GATE_ENFORCE`` when the graph goes on the eligibility path.
    """
    from vocab_mirror.attestation import projection_attested
    if projection_attested(release_id):
        return
    msg = (f'trial omop_* projection has no attestation for release {release_id} '
           '(CB has not published projection-ready)')
    if _PROJECTION_GATE_ENFORCE:
        raise ReleaseMatchFailed(msg)
    logger.warning('vocab activation (observe-only): %s; activating anyway '
                   '(Phase-T not live)', msg)


def _run_release_match_gate(release_id):
    for check in _release_match_checks:
        check(release_id)


def active_release_id():
    """The ``release_id`` readers must pin, or ``None`` when nothing is active.

    ``None`` is the fail-closed signal — callers return 503 / treat the
    vocabulary as unavailable rather than serving an unpinned or stale result.
    """
    return (
        MirrorRelease.objects.using(_ACTIVATION_DB)
        .filter(state=MirrorRelease.ACTIVE)
        .values_list('release_id', flat=True)
        .first()
    )


def activate_release(release_id):
    """Atomically make ``release_id`` the single ACTIVE generation.

    In one ``default``-DB transaction: lock the target row, verify it is READY
    (or already ACTIVE — idempotent), run the release-match gate, demote the
    current ACTIVE generation to SUPERSEDED (its rows are retained for in-flight
    reads), and promote the target. Raises ``ReleaseNotReady`` /
    ``ReleaseMatchFailed`` without leaving the pointer half-flipped; the previous
    generation stays active on any failure.

    **Single-writer expectation.** Activation is meant to run under the #250 sync
    singleton (a Postgres advisory lock), so activations do not overlap. The
    one-active invariant does not depend on that: if two activations *do* race,
    the DB partial-unique index rejects the loser, which is surfaced as a typed
    ``ConcurrentActivation`` (a raw ``IntegrityError`` never escapes this
    contract). Rolling back to a SUPERSEDED generation is deliberately not
    supported here (re-sync/re-activate a fresh READY generation instead).
    """
    with transaction.atomic(using=_ACTIVATION_DB):
        try:
            rel = (MirrorRelease.objects.using(_ACTIVATION_DB)
                   .select_for_update().get(release_id=release_id))
        except MirrorRelease.DoesNotExist:
            raise ReleaseNotReady(f'no MirrorRelease row for release {release_id}')

        if rel.state not in (MirrorRelease.READY, MirrorRelease.ACTIVE):
            raise ReleaseNotReady(
                f'release {release_id} is {rel.state!r}, not READY; cannot activate')

        _run_release_match_gate(release_id)

        try:
            # Demote whatever is currently active (never this row) before
            # promoting, so the partial-unique 'one active' invariant holds per
            # statement.
            (MirrorRelease.objects.using(_ACTIVATION_DB)
             .filter(state=MirrorRelease.ACTIVE)
             .exclude(pk=rel.pk)
             .update(state=MirrorRelease.SUPERSEDED, updated_at=timezone.now()))

            if rel.state != MirrorRelease.ACTIVE:
                rel.state = MirrorRelease.ACTIVE
                rel.activated_at = timezone.now()
                rel.save(using=_ACTIVATION_DB,
                         update_fields=['state', 'activated_at', 'updated_at'])
        except IntegrityError as exc:
            # A concurrent activation promoted first and holds the single
            # 'active' index slot. Fail closed with a typed error, not a raw
            # IntegrityError; the transaction rolls back (previous active kept).
            raise ConcurrentActivation(
                f'release {release_id} lost an activation race') from exc

    logger.info('vocab mirror: activated release %s', release_id)
    return rel
