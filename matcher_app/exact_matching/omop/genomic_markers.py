"""Marker-level evaluation against the existing typed trial envelopes (#5674).

The trial-side envelopes (``omop_molecular_markers_required`` /
``omop_cytogenic_markers_required``) are already populated. This evaluator
reads them against the patient's canonical marker summary, using the exact
code -> concept table in ``marker_concepts`` and the canonical marker code for
envelope entries the compiler left unresolved.

Semantics follow the legacy marker matcher: a required list is "any of these
markers" (the envelope's ``any_of``), an excluded list rejects a match, and an
empty patient summary is unknown rather than a confident negative. The typed
envelope's ``unknown_is_negative`` is honoured when present.

Patient ``genetic_mutations`` findings are deliberately not folded into this
first slice. A marker summary is a curated answer ("TP53 mutation present");
a finding carries its own interpretation and would need its own review before
it could satisfy a marker code.
"""
from exact_matching.omop.marker_concepts import (
    COMPOSITE_MARKER_CODES,
    MARKER_FIELDS,
    marker_codes,
    marker_concept_ids,
)


def _concept_id(value):
    if isinstance(value, bool) or value in (None, ''):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return str(number) if number > 0 else None


def _term_concept(term):
    """The concept id a typed term asserts, or None when the term is unusable."""
    if not isinstance(term, dict):
        return None
    kind = term.get('kind')
    if kind == 'finding':
        feature = term.get('feature') or {}
        return _concept_id(feature.get('concept_id'))
    if kind == 'assertion':
        concept = term.get('concept') or {}
        return _concept_id(concept.get('concept_id'))
    return None


def _term_matches(term, codes, concepts):
    """One typed term against the patient's marker answer.

    A finding is present when the patient's summary carries its concept. The
    envelopes observed for these markers always assert ``present``; a different
    status is not treated as evidence for a marker the patient list does not
    contain.
    """
    if not isinstance(term, dict):
        return False
    concept = _term_concept(term)
    if concept is None:
        return False
    status = term.get('status') or {}
    value = (status.get('value') or {}).get('value')
    if value is not None and str(value).strip().lower() != 'present':
        return False
    if concept in concepts:
        return True
    return False


def _requirement_matches(requirement, codes, concepts, legacy,
                        composite_match=False):
    """Whether one requirement is satisfied by the patient's marker answer.

    Composite patient codes (``notch1or2Mutations``) are not expanded into
    member concepts: they may satisfy a trial that names the same composite
    code, but never a trial that requires one specific member. A specific code
    still satisfies a composite trial through its concept.
    """
    terms = requirement.get('terms') or []
    unresolved = [
        str(code).strip() for code in (requirement.get('unresolved_codes') or [])
        if str(code).strip()
    ]
    if not terms and not unresolved:
        return True
    term_results = [_term_matches(term, codes, concepts) for term in terms]
    code_results = [code in codes for code in unresolved]
    operator = str(requirement.get('operator') or 'any_of').strip().lower()
    if operator == 'all_of':
        results = term_results + code_results
        return all(results) if results else True
    return any(term_results) or any(code_results) or composite_match


def _typed_state(typed, codes, concepts, legacy):
    requirements = [req for req in (typed or []) if isinstance(req, dict)]
    if not requirements:
        return None
    composite_codes = set(codes) & COMPOSITE_MARKER_CODES
    # The legacy list is the only evidence for a composite code; credit it
    # only when the envelope has one requirement, so it cannot satisfy an
    # unrelated second requirement in the same envelope.
    composite_match = bool(composite_codes & set(legacy or [])) and len(requirements) == 1
    for requirement in requirements:
        terms = requirement.get('terms') or []
        unresolved = requirement.get('unresolved_codes') or []
        if not terms and not unresolved:
            continue
        matched = _requirement_matches(
            requirement, codes, concepts, legacy, composite_match,
        )
        if str(requirement.get('role') or 'required').strip().lower() == 'excluded':
            if matched:
                return 'not_matched'
            continue
        if not matched:
            return 'not_matched'
    return 'matched'


def _legacy_state(required, excluded, codes):
    required = [str(code).strip() for code in (required or []) if str(code).strip()]
    excluded = [str(code).strip() for code in (excluded or []) if str(code).strip()]
    if not required and not excluded:
        return 'matched'
    if not codes:
        return 'unknown'
    code_set = set(codes)
    if code_set & set(excluded):
        return 'not_matched'
    if required and not (code_set & set(required)):
        return 'not_matched'
    return 'matched'


def genomic_marker_state(trial, field, markers, is_blank):
    """``matched`` / ``not_matched`` / ``unknown`` for one marker criterion.

    ``field`` is ``'molecular_markers'`` or ``'cytogenic_markers'``. The typed
    envelope wins when present; without it the legacy list is evaluated exactly
    as before, so a deployment that has not populated the envelope keeps its
    current answer.
    """
    legacy_attr, typed_attr, excluded_attr = MARKER_FIELDS[field]
    legacy = getattr(trial, legacy_attr, None) or []
    typed = getattr(trial, typed_attr, None) or []
    excluded = {
        str(code).strip() for code in (getattr(trial, excluded_attr, None) or [])
        if str(code).strip()
    }
    if not legacy and not typed and not excluded:
        return 'matched'

    codes = set(marker_codes(markers))
    if is_blank or not codes:
        # A blank patient summary is incomplete data, not "no markers", unless
        # every requirement explicitly says unknown is negative.
        if typed and all(bool(req.get('unknown_is_negative')) for req in typed):
            return 'not_matched'
        return 'unknown'

    if codes & excluded:
        # The typed envelope carries required terms only. A contradicting
        # legacy exclusion is still a restriction; do not turn a trial that
        # excludes a marker into a match for it just because the two columns
        # disagree (observed on 24917/18489).
        return 'not_matched'

    specific_codes = codes - COMPOSITE_MARKER_CODES
    concepts = marker_concept_ids(specific_codes)
    typed_result = _typed_state(typed, codes, concepts, legacy)
    if typed_result is not None:
        return typed_result
    return _legacy_state(legacy, excluded, codes)
