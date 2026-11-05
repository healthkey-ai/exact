"""Canonical marker codes and the concepts the trial envelopes already use (#5674).

The trial-side typed envelopes (``omop_molecular_markers_required`` /
``omop_cytogenic_markers_required``) were compiled from CB's marker catalog
before this module existed. This table recovers the exact code -> concept pair
from those envelopes instead of inventing a second vocabulary:

* every entry below is observed on trials whose legacy list contains **only**
  that code, with one exception: ``ccnd1Alteration`` is observed together with
  ``t1114`` (14 trials) and its concept is CCND1 ``35963336``;
* the concept id is the envelope's ``feature.concept_id`` for a ``finding``
  term or ``concept.concept_id`` for an ``assertion`` term;
* names and vocabularies are recorded in the comments for review.

Gaps are deliberately not guessed:

* ``bcmaExpression`` appears in ``unresolved_codes`` on two trials. It has no
  exact standard concept, so it is minted under ``HK-Genomics``
  (``2100007900``, ``HK-MARKER:BCMA-EXPRESSION``); the trial envelope stays on
  the canonical code until it is recompiled to emit that feature.
* ``del13q`` is a patient-side cytogenetic value with no observed trial
  envelope; it stays on the legacy string path.
* ``1q21`` / ``1q21Amplification`` already use the local ``HK-Labs`` concept
  ``2100007862`` in the envelope. It is recorded as-is; minting a reviewed
  HK-Genomics concept is a separate decision (#5674).

Do not add a code here just because a string looks similar. An entry is a
claim that the patient marker and the trial envelope mean the same thing.

The table is shared by the molecular and cytogenic fields because several
identities have both CB spellings (``1q21``/``1q21Amplification``,
``t414``/``t414Fgfr3``). The evaluator is always called with one field's
patient summary, so an alias does not by itself import a marker from the other
axis; a caller that mixes the two is outside the contract.
"""

#: Canonical CB marker code -> the concept ids the typed trial envelope uses.
#: Multiple ids mean the envelope has one term per id (OR within the code).
MARKER_CONCEPTS = {
    # Molecular markers.
    'tp53Mutation': ('35948926',),          # TP53 gene variant (HGNC 11998)
    'krasMutation': ('35948791',),          # KRAS gene variant (HGNC 6407)
    'nrasMutation': ('35950119',),          # NRAS gene variant (HGNC 7989)
    'brafMutation': ('19643612',),          # BRAF exon 15 mutation
    'notch1Mutation': ('35947617',),        # NOTCH1 gene variant (HGNC 7881)
    'ccnd1Alteration': ('35963336',),       # CCND1 gene variant (HGNC 1582)
    'cd38Expression': ('35976963',),        # CD38 protein expression
    # Locally minted under HK-Genomics (#5674): the loaded vocabulary has no
    # exact BCMA expression concept. The trial envelope still carries the
    # canonical code as unresolved until it is recompiled to emit this feature.
    'bcmaExpression': ('2100007900',),      # HK-MARKER:BCMA-EXPRESSION
    'mycRearrangements': ('19643476',),     # MYC gene rearrangement
    'ighRearrangements': ('35976995',),     # IGH gene rearrangement
    'del17p13': ('19641187',),              # del(17p) structural variant
    't1114': ('19642084',),                 # t(11;14) structural variant
    't414Fgfr3': ('19641006',),             # t(4;14) structural variant
    't1416': ('19476524',),                 # t(14;16) structural variant
    '1q21': ('2100007862',),                # HK-Labs gain 1q21
    'complexKaryotype': ('36032861',),      # LOINC LA31354-6 answer
    # Cytogenic markers (same identities, CB's cytogenic spellings).
    't414': ('19641006',),                  # t(4;14) structural variant
    '1q21Amplification': ('2100007862',),   # HK-Labs gain 1q21
    # An assertion, not a finding: SNOMED 55597007 Hyperploidy.
    'hyperdiploidy': ('4208087',),
}

#: Composite patient codes. They mean "one of these genes", so they must not
#: be expanded into every member concept: a patient recorded as
#: ``notch1or2Mutations`` may have NOTCH1, NOTCH2, or both, and therefore
#: cannot prove a trial that requires only NOTCH1. The reverse direction is
#: valid: a specific ``notch1Mutation`` does satisfy a NOTCH1/NOTCH2 trial.
#: Composite codes match a trial only when that trial's own marker list names
#: the same composite code.
COMPOSITE_MARKER_CODES = frozenset({
    'atmOrAtrMutations',
    'notch1or2Mutations',
})

#: The member concepts a composite trial envelope uses. These are trial-side
#: identities only: a patient composite code is not expanded into them, but the
#: readiness audit must recognise the envelope as mapped.
COMPOSITE_MARKER_CONCEPTS = {
    'atmOrAtrMutations': ('35960740', '35963934'),   # ATM (795), ATR (882)
    'notch1or2Mutations': ('35947617', '35960756'),  # NOTCH1 (7881), NOTCH2 (7882)
}

#: The full trial-side concept space: specific markers plus the member
#: concepts of the composite requirements.
TRIAL_MARKER_CONCEPTS = {**MARKER_CONCEPTS, **COMPOSITE_MARKER_CONCEPTS}

#: Codes the envelopes list as unresolved. They are matched by the canonical
#: marker code itself, never by a guessed concept. ``bcmaExpression`` also has
#: a local HK-Genomics identity now; it stays here until the trial envelope is
#: recompiled to emit that feature.
UNRESOLVED_MARKER_CODES = frozenset({'bcmaExpression'})

#: Marker criteria and the legacy/typed columns that carry them.
MARKER_FIELDS = {
    'molecular_markers': (
        'molecular_markers_required', 'omop_molecular_markers_required',
        'molecular_markers_excluded',
    ),
    'cytogenic_markers': (
        'cytogenic_markers_required', 'omop_cytogenic_markers_required',
        'cytogenic_markers_excluded',
    ),
}

#: Every typed column the evaluator reads, for the search-side empty test.
MARKER_COLUMNS = tuple(
    column
    for columns in MARKER_FIELDS.values()
    for column in columns
)


def marker_concept_ids(codes):
    """The concept ids a set of canonical marker codes stands for."""
    concepts = set()
    for code in codes or ():
        concepts.update(MARKER_CONCEPTS.get(str(code).strip(), ()))
    return concepts


def marker_codes(value):
    """Normalize a patient marker value (CSV string or list) to canonical codes."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        raw = str(value).split(',')
    return [
        str(code).strip() for code in raw
        if str(code).strip()
    ]
