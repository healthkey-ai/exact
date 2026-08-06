"""Sync-loader tests (#250 / ADR 0002) — load, verify, activate, fail closed.

Uses a fake vocab client that yields NDJSON rows (plus the ``__done`` sentinel),
so the load/verify/activate orchestration is exercised without a network.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from vocab_mirror.activation import active_release_id
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.promop_vocab_client import (
    LatestRelease,
    VocabReleaseSuperseded,
    VocabSyncError,
)
from vocab_mirror.sync import sync_vocab_mirror

pytestmark = pytest.mark.django_db


VOCAB_ROWS = [{'vocabulary_id': 'RxNorm', 'vocabulary_name': 'RxNorm',
               'vocabulary_reference': None, 'vocabulary_version': '2026',
               'vocabulary_concept_id': 44819104}]
CONCEPT_ROWS = [{'concept_id': 100, 'concept_name': 'Bortezomib', 'domain_id': 'Drug',
                 'vocabulary_id': 'RxNorm', 'concept_class_id': 'Ingredient',
                 'standard_concept': 'S', 'concept_code': '1',
                 'valid_start_date': '1970-01-01', 'valid_end_date': '2099-12-31',
                 'invalid_reason': None, 'source': None, 'ignored_extra_col': 'drop me'}]
REL_ROWS = [{'concept_id_1': 10, 'concept_id_2': 100, 'relationship_id': 'Has component',
             'valid_start_date': '1970-01-01', 'valid_end_date': '2099-12-31',
             'invalid_reason': None}]
ANC_ROWS = [{'ancestor_concept_id': 10, 'descendant_concept_id': 100,
             'min_levels_of_separation': 1, 'max_levels_of_separation': 1}]
_SNAPSHOTS = {'vocabulary': VOCAB_ROWS, 'concept': CONCEPT_ROWS,
              'concept_relationship': REL_ROWS, 'concept_ancestor': ANC_ROWS}


def _manifest(release_id=7, row_counts=None):
    if row_counts is None:
        row_counts = {t: len(rows) for t, rows in _SNAPSHOTS.items()}
    return {'id': release_id, 'row_counts': row_counts, 'checksums': {}}


class FakeVocabClient:
    def __init__(self, manifest=None, not_modified=False, etag='etag-1',
                 snapshots=None, truncate=None, bad_sentinel=None):
        self._manifest = manifest if manifest is not None else _manifest()
        self._not_modified = not_modified
        self._etag = etag
        self._snapshots = snapshots if snapshots is not None else _SNAPSHOTS
        self._truncate = truncate or set()      # omit the sentinel => truncated
        self._bad_sentinel = bad_sentinel or set()  # sentinel without an int `rows`

    def get_latest_release(self, if_none_match=None):
        if self._not_modified:
            return LatestRelease(not_modified=True, manifest=None, etag=if_none_match)
        return LatestRelease(not_modified=False, manifest=self._manifest, etag=self._etag)

    def stream_snapshot(self, release_id, table):
        rows = self._snapshots.get(table, [])
        for row in rows:
            yield row
        if table in self._truncate:
            return
        yield {'__done': True} if table in self._bad_sentinel else {'__done': True, 'rows': len(rows)}


def _seed_full_release(release_id=7, state=MirrorRelease.READY, etag='etag-1'):
    """A complete generation (all four tables) + a MirrorRelease row."""
    MirrorRelease.objects.create(release_id=release_id, state=state, etag=etag)
    MirrorVocabulary.objects.create(release_id=release_id, vocabulary_id='RxNorm',
                                    vocabulary_name='RxNorm', vocabulary_concept_id=1)
    MirrorConcept.objects.create(release_id=release_id, concept_id=100, concept_name='x',
                                 domain_id='Drug', vocabulary_id='RxNorm',
                                 concept_class_id='Ingredient', concept_code='1')
    MirrorConceptRelationship.objects.create(release_id=release_id, concept_id_1=10,
                                             concept_id_2=100, relationship_id='Has component')
    MirrorConceptAncestor.objects.create(release_id=release_id, ancestor_concept_id=10,
                                         descendant_concept_id=100,
                                         min_levels_of_separation=1, max_levels_of_separation=1)


class TestHappyPath:
    def test_full_sync_loads_verifies_and_activates(self):
        outcome = sync_vocab_mirror(client=FakeVocabClient())
        assert outcome.status == 'synced'
        assert outcome.release_id == 7
        rel = MirrorRelease.objects.get(release_id=7)
        assert rel.state == MirrorRelease.ACTIVE
        assert active_release_id() == 7
        # rows loaded, release-tagged, extra column dropped, date coerced
        c = MirrorConcept.objects.get(release_id=7, concept_id=100)
        assert c.concept_name == 'Bortezomib'
        assert c.valid_start_date == date(1970, 1, 1)
        assert MirrorConceptRelationship.objects.filter(release_id=7).count() == 1
        assert MirrorConceptAncestor.objects.filter(release_id=7).count() == 1
        assert MirrorVocabulary.objects.filter(release_id=7).count() == 1

    def test_no_activate_leaves_release_ready(self):
        outcome = sync_vocab_mirror(client=FakeVocabClient(), activate=False)
        assert outcome.status == 'synced'
        assert MirrorRelease.objects.get(release_id=7).state == MirrorRelease.READY
        assert active_release_id() is None


class TestSkips:
    def test_not_modified_304_is_a_noop(self):
        outcome = sync_vocab_mirror(client=FakeVocabClient(not_modified=True))
        assert outcome.status == 'unchanged'
        assert not MirrorRelease.objects.exists()

    def test_already_active_release_is_skipped(self):
        _seed_full_release(7, state=MirrorRelease.ACTIVE)
        before = MirrorConcept.objects.get(release_id=7).concept_id
        outcome = sync_vocab_mirror(client=FakeVocabClient())
        assert outcome.status == 'already_synced'
        # not reloaded — the pre-seeded concept row is untouched
        assert MirrorConcept.objects.get(release_id=7).concept_id == before

    def test_ready_release_is_recovered_by_activation(self):
        """A prior run that loaded (READY) but whose activation was stranded must
        self-recover on the next run — re-activate, do not re-download or 503
        forever (#250 review P1)."""
        _seed_full_release(7, state=MirrorRelease.READY)
        assert active_release_id() is None
        outcome = sync_vocab_mirror(client=FakeVocabClient())
        assert outcome.status == 'activated'
        assert active_release_id() == 7


class TestFailClosed:
    def test_truncated_stream_fails_rolls_back_and_keeps_etag_unadvanced(self):
        client = FakeVocabClient(truncate={'concept'})
        with pytest.raises(VocabSyncError):
            sync_vocab_mirror(client=client)
        rel = MirrorRelease.objects.get(release_id=7)
        assert rel.state == MirrorRelease.FAILED
        assert MirrorConcept.objects.filter(release_id=7).count() == 0  # rolled back
        assert active_release_id() is None                              # not activated
        # a FAILED release must not become the "last processed" etag -> next run re-polls
        from vocab_mirror.sync import _last_processed_etag
        assert _last_processed_etag() is None

    def test_manifest_row_count_mismatch_fails(self):
        client = FakeVocabClient(manifest=_manifest(row_counts={'vocabulary': 1, 'concept': 5,
                                                                'concept_relationship': 1,
                                                                'concept_ancestor': 1}))
        with pytest.raises(VocabSyncError):
            sync_vocab_mirror(client=client)
        assert MirrorRelease.objects.get(release_id=7).state == MirrorRelease.FAILED
        assert MirrorConcept.objects.filter(release_id=7).count() == 0


    def test_bad_sentinel_without_rows_count_fails(self):
        client = FakeVocabClient(bad_sentinel={'concept'})
        with pytest.raises(VocabSyncError):
            sync_vocab_mirror(client=client)
        assert MirrorRelease.objects.get(release_id=7).state == MirrorRelease.FAILED

    def test_malformed_manifest_without_id_raises_typed(self):
        client = FakeVocabClient(manifest={'row_counts': {}, 'checksums': {}})  # no 'id'
        with pytest.raises(VocabSyncError):
            sync_vocab_mirror(client=client)
        assert not MirrorRelease.objects.exists()

    def test_client_error_propagates_and_does_not_activate(self):
        class _BoomClient:
            def get_latest_release(self, if_none_match=None):
                raise VocabSyncError('could not mint a vocab OAuth token (fail closed)')

        with pytest.raises(VocabSyncError):
            sync_vocab_mirror(client=_BoomClient())
        assert active_release_id() is None


class TestSubsetNotActivated:
    def test_subset_load_stays_staging_not_ready(self):
        # A subset load is an incomplete generation: it must stay STAGING (never
        # READY), so it is neither activated nor wedged — a later full sync can
        # restage it.
        outcome = sync_vocab_mirror(client=FakeVocabClient(), tables=['concept'])
        assert outcome.status == 'partial'
        assert MirrorRelease.objects.get(release_id=7).state == MirrorRelease.STAGING
        assert active_release_id() is None
        assert MirrorConcept.objects.filter(release_id=7).exists()
        assert not MirrorConceptRelationship.objects.filter(release_id=7).exists()

    def test_subset_then_full_sync_recovers_and_activates(self):
        # The wedge regression: a subset (STAGING) followed by a full sync must
        # restage + fully load + activate — never get stuck on an incomplete READY.
        sync_vocab_mirror(client=FakeVocabClient(), tables=['concept'])
        outcome = sync_vocab_mirror(client=FakeVocabClient())  # full
        assert outcome.status == 'synced'
        assert active_release_id() == 7
        assert MirrorConceptRelationship.objects.filter(release_id=7).exists()


class TestIdempotentRestage:
    def test_restage_deletes_stale_partial_rows(self):
        # a previous FAILED attempt left partial rows + a FAILED release row
        MirrorRelease.objects.create(release_id=7, state=MirrorRelease.FAILED)
        MirrorConcept.objects.create(release_id=7, concept_id=999, concept_name='stale',
                                     domain_id='Drug', vocabulary_id='x',
                                     concept_class_id='y', concept_code='z')
        outcome = sync_vocab_mirror(client=FakeVocabClient())
        assert outcome.status == 'synced'
        # stale row gone, only the fresh generation's rows remain (no duplication)
        assert MirrorConcept.objects.filter(release_id=7).count() == 1
        assert not MirrorConcept.objects.filter(release_id=7, concept_id=999).exists()


class TestSingletonLock:
    def test_lock_not_acquired_returns_locked_without_loading(self):
        fake_cur = MagicMock()
        fake_cur.fetchone.return_value = [False]  # pg_try_advisory_lock -> False
        fake_conn = MagicMock()
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur
        with patch('vocab_mirror.sync.connections') as conns:
            conns.__getitem__.return_value = fake_conn
            outcome = sync_vocab_mirror(client=FakeVocabClient())
        assert outcome.status == 'locked'
        assert not MirrorRelease.objects.exists()  # never touched the release table


class TestCopyLoad:
    """COPY-load correctness (#256): escaping + NULL-vs-empty distinction."""

    def test_copy_escapes_special_chars_and_null_vs_empty(self):
        tricky = [{
            'concept_id': 200,
            'concept_name': 'a\tb\nc\\d',       # tab, newline, backslash must survive
            'domain_id': 'Drug',
            'vocabulary_id': 'RxNorm',
            'concept_class_id': '',             # empty string must NOT become NULL
            'standard_concept': None,           # NULL must stay NULL
            'concept_code': '2',
            'valid_start_date': '1970-01-01',   # ISO date -> DateField
            'valid_end_date': None,             # NULL date
            'invalid_reason': None,
            'source': None,
        }]
        snapshots = dict(_SNAPSHOTS, concept=tricky)
        row_counts = {t: len(r) for t, r in snapshots.items()}
        client = FakeVocabClient(manifest=_manifest(row_counts=row_counts),
                                 snapshots=snapshots)

        outcome = sync_vocab_mirror(client=client)

        assert outcome.status == 'synced'
        c = MirrorConcept.objects.get(concept_id=200)
        assert c.concept_name == 'a\tb\nc\\d'   # special chars round-tripped
        assert c.concept_class_id == ''          # empty string, not NULL
        assert c.standard_concept is None        # NULL preserved
        assert c.valid_start_date == date(1970, 1, 1)
        assert c.valid_end_date is None          # NULL date preserved


class TestSupersededMidSync:
    """promop snapshots are latest-only (#373/#301): a mid-sync 409 must re-resolve
    /latest and restart, never mark the release FAILED."""

    def test_supersede_reresolves_latest_and_activates_new_release(self):
        class _Client:
            def __init__(self):
                self.polls = 0

            def get_latest_release(self, if_none_match=None):
                self.polls += 1
                rid = 10 if self.polls == 1 else 11
                return LatestRelease(not_modified=False,
                                     manifest=_manifest(release_id=rid), etag=f'e{rid}')

            def stream_snapshot(self, release_id, table):
                # Eager (like the real client): raise at call time, not inside the
                # generator, so a supersede never reaches the COPY.
                if release_id == 10:            # stale mid-sync
                    raise VocabReleaseSuperseded('release 10 superseded')
                return self._rows(table)         # release 11 streams normally

            @staticmethod
            def _rows(table):
                rows = _SNAPSHOTS[table]
                yield from rows
                yield {'__done': True, 'rows': len(rows)}

        outcome = sync_vocab_mirror(client=_Client())

        assert outcome.status == 'synced'
        assert outcome.release_id == 11
        assert active_release_id() == 11
        assert not MirrorRelease.objects.filter(release_id=10).exists()  # discarded, not FAILED

    def test_relentless_supersede_gives_up_gracefully(self):
        class _Client:
            def get_latest_release(self, if_none_match=None):
                return LatestRelease(not_modified=False,
                                     manifest=_manifest(release_id=20), etag='e20')

            def stream_snapshot(self, release_id, table):
                raise VocabReleaseSuperseded('always superseded')

        outcome = sync_vocab_mirror(client=_Client())

        assert outcome.status == 'superseded'
        assert not MirrorRelease.objects.exists()  # every stale staging discarded
