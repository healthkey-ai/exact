"""Canonical trial-projection checksum — the CB↔EXACT contract (ADR 0002 §Gate 1 gap #1,
ADR 0003 Option D).

EXACT recomputes this from its OWN trial rows at mirror activation and verifies it against
the ``checksum`` CancerBot published in the ``ProjectionAttestation`` for the release, so a
stale / partial / mid-reload trial projection cannot silently pass the activation gate.
For the check to mean anything, **CB MUST compute the byte-identical value** over its own
trial rows for the same release — hence this domain is a frozen cross-repo contract:

Domain (do NOT change without a coordinated CB change):
- **Universe:** every ``Trial`` row (``Trial.objects.all()``) — the complete projection.
- **Per-trial tuple:** ``[code, required, excluded]`` where ``code`` is the unique business
  key (CB-portable; EXACT's local ``pk`` is deliberately NOT used) and
  ``required`` / ``excluded`` are the ``omop_therapy_types_{required,excluded}`` values
  NORMALIZED to a sorted list of unique decimal strings. Normalization is essential: the
  matcher treats these as SETS (``has_any_keys``), so the checksum must be independent of
  the stored list's order and duplicates on either side.
- **Order:** the per-trial tuples sorted by ``code`` **in-process, by Unicode codepoint**
  (Python's default ``sort``) — NOT the database's ``ORDER BY`` (which depends on the DB's
  ``LC_COLLATE`` and would diverge across deployments / from CB's recompute).
- **Values:** ``omop_therapy_types_*`` are integer concept_ids; ``_normalize`` stringifies
  them (a non-integer value would serialize differently and is out of contract).
- **Serialization:** compact JSON (``separators=(',', ':')``, ``ensure_ascii=True``) of the
  ordered list.
- **Digest:** lowercase hex ``sha256`` of the UTF-8 bytes.

The function is pure / read-only. It does NOT hardcode ``.using(...)``: ``Trial`` is routed
to the optional ``trials`` DB when configured, else ``default`` — let the router pick.
"""
import hashlib
import json


def _normalize(values):
    """A JSONField list → sorted list of unique decimal-strings (set semantics)."""
    return sorted({str(v) for v in (values or [])})


def compute_trial_projection_checksum():
    """Return ``(sha256_hex, trial_count)`` over the current trial projection.

    ``trial_count`` is the number of ``Trial`` rows hashed — CB publishes it alongside the
    checksum so the gate can also catch a truncated/short projection.
    """
    from trials.models import Trial
    rows = Trial.objects.values_list(
        'code', 'omop_therapy_types_required', 'omop_therapy_types_excluded')
    tuples = [[code, _normalize(required), _normalize(excluded)]
              for code, required, excluded in rows.iterator()]
    # Sort in-process by Unicode codepoint (portable) — NOT via the DB's ORDER BY, whose
    # collation is environment-dependent and would make the "frozen" hash non-reproducible
    # across deployments and CB.
    tuples.sort(key=lambda t: t[0])
    payload = json.dumps(tuples, separators=(',', ':'), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return digest, len(tuples)
