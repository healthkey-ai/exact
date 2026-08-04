"""Local mirror of promop's OMOP vocabulary tables (ADR 0002; epic #246, #248).

EXACT keeps a **release-pinned local mirror** of the OMOP vocabulary tables it
needs and traverses the concept graph locally, instead of querying promop's
concept-graph API per request (the API+cache approach of #234 is retired in
#251). Rows are synced from promop's snapshot protocol (promop#334) — see #250.

Design (see docs/adr/0002-vocab-mirror-consumer-consistency.md):

- **Generation-tagged, not mutated in place.** Every row carries a
  ``release_id`` (the promop VocabularyRelease id). A new release loads into new
  rows; the active generation is chosen by a separate pointer (the release
  contract + atomic activation land in #249). Readers pin one ``release_id`` for
  a whole request, so a mid-sync swap never yields a torn or mixed-release read.
- **Flat copies, no ForeignKeys.** These tables are bulk-loaded from a snapshot
  (NDJSON with keys = OMOP column names); cross-row/cross-release referential
  integrity is neither loadable mid-stream nor wanted. Traversal is by plain
  concept_id columns, filtered by the pinned release.
- **This app is NOT the ``trials`` app** — the DB router sends ``trials`` models
  to the optional read-only ``trials`` DB (migrations disallowed by default),
  whereas a mirror we own must be writable. A non-``trials`` app routes to
  ``default`` (see ``exact/db_router.py``), which is where the mirror belongs.

Indexes are ``release_id``-leading and match the traversal access patterns
(descendants/ancestors of a source concept, filtered by relationship) so a
pinned-release query never scans across generations. List-partitioning by
``release_id`` is a deferred (v2) optimization — see the ADR.
"""
from django.db import models
from django.db.models import Q


class MirrorRelease(models.Model):
    """A synced vocabulary release generation and its lifecycle state (#249).

    One row per promop ``VocabularyRelease`` that EXACT has (partially or fully)
    synced. The ``state`` machine gates what readers may pin::

        STAGING  (loading, #250)
          → READY      (downloaded + row_counts/checksums verified)
          → ACTIVE     (the single generation readers pin)
          → SUPERSEDED (kept for the retention window so in-flight reads finish)
        (any → FAILED  on a verification/load failure; never activated)

    A **partial-unique constraint** guarantees at most one ``ACTIVE`` release, so
    the atomic activation flip (see ``activation.activate_release``) cannot leave
    two live generations. Readers bind ``active_release_id()`` once per request
    and fail closed when it is ``None``. The DB enforces only the *one-active*
    invariant; the STAGING→READY→ACTIVE transitions themselves are enforced by
    the activation service (``activation.py``), which is the only supported
    writer of ``state`` — direct ORM writes bypass the state machine.

    The manifest fields (``row_counts`` / ``checksums`` / ``manifest``) are the
    promop release manifest (promop#334); the loader (#250) fills them and moves
    STAGING → READY. Completeness is enforced at load by the ``__done`` sentinel
    + ``row_counts`` cross-check; the ``checksums`` are recorded for provenance —
    byte-integrity verification against them is blocked cross-repo (promop's
    ``checksums`` are ``{count, min_ctid, max_ctid}``, and ctids are meaningless to
    a consumer, so real verification needs promop to emit a content hash; see the
    ``sync`` module docstring), not yet enforced.
    """

    STAGING = 'staging'
    READY = 'ready'
    ACTIVE = 'active'
    SUPERSEDED = 'superseded'
    FAILED = 'failed'
    STATE_CHOICES = [
        (STAGING, STAGING), (READY, READY), (ACTIVE, ACTIVE),
        (SUPERSEDED, SUPERSEDED), (FAILED, FAILED),
    ]

    release_id = models.BigIntegerField(unique=True)
    etag = models.CharField(max_length=255, null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STAGING, db_index=True)
    # Per-table expected counts + sha256 from the release manifest (promop#334).
    row_counts = models.JSONField(default=dict, blank=True)
    checksums = models.JSONField(default=dict, blank=True)
    # Full release manifest blob, kept for provenance/audit.
    manifest = models.JSONField(default=dict, blank=True)
    loaded_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # At most one ACTIVE generation, enforced by the DB (a partial unique
            # index over the constant ACTIVE state).
            models.UniqueConstraint(
                fields=['state'], condition=Q(state='active'),
                name='uq_one_active_mirror_release',
            ),
        ]

    def __str__(self):
        return f'release {self.release_id} [{self.state}]'


class MirrorVocabulary(models.Model):
    """One OMOP ``vocabulary`` row, tagged to a mirror release generation."""

    # No standalone index on release_id — the unique (release_id, vocabulary_id)
    # constraint's B-tree already serves release_id-prefix lookups.
    release_id = models.BigIntegerField()
    vocabulary_id = models.CharField(max_length=20)
    vocabulary_name = models.CharField(max_length=255)
    vocabulary_reference = models.CharField(max_length=255, null=True, blank=True)
    vocabulary_version = models.CharField(max_length=255, null=True, blank=True)
    vocabulary_concept_id = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['release_id', 'vocabulary_id'], name='uq_mvocab_rel_vocab',
            ),
        ]

    def __str__(self):
        return f'{self.vocabulary_id}@{self.release_id}'


class MirrorConcept(models.Model):
    """One OMOP ``concept`` row, tagged to a mirror release generation.

    Uniquely keyed by ``(release_id, concept_id)`` so several generations can
    coexist during a swap + retention window. Powers title resolution (the
    ``OmopConcept`` replacement, #251) and is the node table for traversal.
    """

    release_id = models.BigIntegerField()
    concept_id = models.IntegerField()
    concept_name = models.CharField(max_length=255)
    domain_id = models.CharField(max_length=20)
    vocabulary_id = models.CharField(max_length=20)
    concept_class_id = models.CharField(max_length=20)
    standard_concept = models.CharField(max_length=1, null=True, blank=True)
    concept_code = models.CharField(max_length=50)
    valid_start_date = models.DateField(null=True, blank=True)
    valid_end_date = models.DateField(null=True, blank=True)
    invalid_reason = models.CharField(max_length=1, null=True, blank=True)
    # promop's snapshot exposes `source` (HealthKey vs Athena-loaded); kept for
    # provenance and the concept-table `?source=` filter (promop#334).
    source = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['release_id', 'concept_id'], name='uq_mconcept_rel_cid',
            ),
        ]
        indexes = [
            models.Index(fields=['release_id', 'vocabulary_id'], name='ix_mconcept_rel_vocab'),
        ]

    def __str__(self):
        return f'{self.concept_id}:{self.concept_name}@{self.release_id}'


class MirrorConceptRelationship(models.Model):
    """One OMOP ``concept_relationship`` row, tagged to a release generation.

    The forward index (``release_id, concept_id_1, relationship_id``) serves
    downstream traversal (e.g. a regimen concept to its component concepts via a
    given relationship); the reverse index serves the upstream direction. No
    uniqueness constraint on ~18M rows — the snapshot is authoritative and its
    completeness is verified at load (row_counts + checksums, promop#334 / #250).
    """

    release_id = models.BigIntegerField()
    concept_id_1 = models.IntegerField()
    concept_id_2 = models.IntegerField()
    relationship_id = models.CharField(max_length=20)
    valid_start_date = models.DateField(null=True, blank=True)
    valid_end_date = models.DateField(null=True, blank=True)
    invalid_reason = models.CharField(max_length=1, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['release_id', 'concept_id_1', 'relationship_id'], name='ix_mcr_rel_c1_rid'),
            models.Index(fields=['release_id', 'concept_id_2', 'relationship_id'], name='ix_mcr_rel_c2_rid'),
        ]

    def __str__(self):
        return f'{self.concept_id_1}-[{self.relationship_id}]->{self.concept_id_2}@{self.release_id}'


class MirrorConceptAncestor(models.Model):
    """One OMOP ``concept_ancestor`` row, tagged to a release generation.

    The ancestor index answers "descendants of X" and the descendant index
    answers "ancestors of X", both scoped to one release.
    """

    release_id = models.BigIntegerField()
    ancestor_concept_id = models.IntegerField()
    descendant_concept_id = models.IntegerField()
    min_levels_of_separation = models.IntegerField()
    max_levels_of_separation = models.IntegerField()

    class Meta:
        indexes = [
            models.Index(fields=['release_id', 'ancestor_concept_id'], name='ix_mca_rel_anc'),
            models.Index(fields=['release_id', 'descendant_concept_id'], name='ix_mca_rel_desc'),
        ]

    def __str__(self):
        return f'{self.ancestor_concept_id}=>{self.descendant_concept_id}@{self.release_id}'


class ComponentCategoryOmopLookup(models.Model):
    """Flat ``component concept_id → CB category codes`` lookup (#4503; moved #262).

    CB generates this from its internal TherapyComponent → TherapyComponentCategory
    M2M graph; EXACT reads it to resolve drug-class "types" in OMOP mode WITHOUT the
    full internal graph. One row per component ``omop_concept_id`` (RxNorm
    ingredient); ``category_codes`` is the sorted, de-duplicated union of CB category
    codes. Populated by ``rebuild_component_category_omop_lookup`` /
    ``sync_component_category_lookup``.

    **Why it lives here, not in ``trials`` (#262 / ADR 0002 B′).** It is a
    release-gated derived artifact now, so it must be writable on ``default`` — the
    ``trials`` app routes to the (optional) read-only ``trials`` DB. Its *content* is
    CB-authored and promop-release-**independent** (EXACT ADR 0001 decision A —
    component→type is deliberately NOT OMOP-mapped); the only tie to a mirror release
    is that its keys are concept_ids that must exist in the pinned release.
    Consistency is therefore enforced at activation by the release-match gate
    (``activation._component_lookup_matches_release``) against a single, atomically
    published payload — **not** by per-row release tagging (which, for a
    release-independent artifact, would be fake generations).
    """

    component_concept_id = models.BigIntegerField(primary_key=True)
    category_codes = models.JSONField(blank=True, null=False, default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.component_concept_id} -> {self.category_codes}'


class ComponentLookupStamp(models.Model):
    """Singleton provenance stamp for :class:`ComponentCategoryOmopLookup` (#262 / B′).

    Records which mirror ``release_id`` the current lookup payload was last rebuilt
    and validated against. Written **atomically in the same transaction** as the
    payload rebuild (``sync_component_category_lookup``), so a reader never sees a
    payload/stamp mismatch. Read by the activation release-match gate, which refuses
    to activate a release R unless the lookup is stamped for R (proof the publish
    pipeline ran for R) and every lookup concept_id exists in R's mirror concepts
    (referential coverage).

    Enforced-singleton: ``singleton`` is a constant ``True`` with a unique index, so
    at most one row ever exists (upserted, never duplicated).
    """

    singleton = models.BooleanField(default=True, unique=True)
    release_id = models.BigIntegerField()
    built_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'component-lookup stamped @ release {self.release_id}'
