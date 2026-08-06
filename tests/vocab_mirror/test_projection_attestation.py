"""Trial-projection attestation + its activation gate (#265 / ADR 0002 mechanism #4).

CB publishes an attestation into EXACT's default DB; the activation gate reads it
locally (no cross-DB read). The gate is OBSERVE-ONLY until Phase-T (#263), so a
missing attestation logs but never blocks activation today.
"""
import logging

import pytest

from vocab_mirror import activation
from vocab_mirror.activation import ReleaseMatchFailed, activate_release, active_release_id
from vocab_mirror.attestation import projection_attested, publish_projection_attestation
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
    ProjectionAttestation,
)

pytestmark = pytest.mark.django_db


def _ready_release(rid):
    MirrorRelease.objects.create(release_id=rid, state=MirrorRelease.READY)
    MirrorConcept.objects.create(release_id=rid, concept_id=1, concept_name='x',
                                 domain_id='Drug', vocabulary_id='RxNorm',
                                 concept_class_id='Ingredient', concept_code='c')
    MirrorVocabulary.objects.create(release_id=rid, vocabulary_id='RxNorm',
                                    vocabulary_name='RxNorm', vocabulary_concept_id=1)
    MirrorConceptRelationship.objects.create(release_id=rid, concept_id_1=1,
                                             concept_id_2=2, relationship_id='Subsumes')
    MirrorConceptAncestor.objects.create(release_id=rid, ancestor_concept_id=1,
                                         descendant_concept_id=2,
                                         min_levels_of_separation=1, max_levels_of_separation=1)


# ── publish / attested ───────────────────────────────────────────────────────

def test_publish_upserts_one_row_per_release():
    publish_projection_attestation(5, run_id='r1', trial_count=100, checksum='abc')
    publish_projection_attestation(5, run_id='r2', trial_count=110)  # re-publish
    assert ProjectionAttestation.objects.count() == 1
    row = ProjectionAttestation.objects.get(release_id=5)
    assert row.run_id == 'r2' and row.trial_count == 110
    assert projection_attested(5) and not projection_attested(6)


# ── gate: observe-only (default, pre-Phase-T) ────────────────────────────────

def test_missing_attestation_does_not_block_activation(caplog):
    _ready_release(1)
    with caplog.at_level(logging.WARNING, logger='vocab_mirror.activation'):
        activate_release(1)                       # no attestation → observe-only
    assert active_release_id() == 1               # activated anyway
    assert any('observe-only' in r.message and 'projection' in r.message
               for r in caplog.records)


def test_attested_release_activates_cleanly(caplog):
    _ready_release(1)
    publish_projection_attestation(1, run_id='cb-run', trial_count=42)
    with caplog.at_level(logging.WARNING, logger='vocab_mirror.activation'):
        activate_release(1)
    assert active_release_id() == 1
    assert not any('projection' in r.message for r in caplog.records)  # no warning


# ── gate: enforcing (Phase-T on) ─────────────────────────────────────────────

def test_enforcing_blocks_activation_without_attestation(monkeypatch):
    monkeypatch.setattr(activation, '_PROJECTION_GATE_ENFORCE', True)
    _ready_release(1)
    with pytest.raises(ReleaseMatchFailed):
        activate_release(1)
    assert active_release_id() is None            # blocked, fail-closed


def test_enforcing_allows_activation_with_attestation(monkeypatch):
    monkeypatch.setattr(activation, '_PROJECTION_GATE_ENFORCE', True)
    _ready_release(1)
    publish_projection_attestation(1)
    activate_release(1)
    assert active_release_id() == 1


# ── management command ───────────────────────────────────────────────────────

def test_command_publishes_attestation():
    from io import StringIO

    from django.core.management import CommandError, call_command
    call_command('publish_projection_attestation', release_id=9, run_id='x',
                 trial_count=7, stdout=StringIO())
    row = ProjectionAttestation.objects.get(release_id=9)
    assert row.run_id == 'x' and row.trial_count == 7
    with pytest.raises(CommandError):
        call_command('publish_projection_attestation', release_id=-1, stdout=StringIO())
