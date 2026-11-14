"""Gene identity for genomic criteria (#5673).

A gene is identified by the standard **OMOP Genomic** Measurement concept whose
``concept_code`` is the HGNC id. This is the same crosswalk PROMOP catalog v3
writes into a finding's ``gene_concept_id`` (cancerbot-org/cancerbot#5673); the
map is duplicated here because the standalone matcher package must not import
PROMOP.

The trial's typed ``omop_mutation_genes_required`` envelope carries those ids.
This module only *reads* the envelope — the ``cb-trial-criteria-1`` writer stays
in CB — and reports when it cannot, so the matcher falls back to the legacy
symbol columns rather than guessing.
"""
from django.conf import settings

SCHEMA = 'cb-trial-criteria-1'

# canonical symbol -> (HGNC id, OMOP Genomic concept id)
GENE_CONCEPTS = {
    'ATM': ('HGNC:795', 35960740),
    'BCL2': ('HGNC:990', 35958594),
    'BRAF': ('HGNC:1097', 35948173),
    'BRCA1': ('HGNC:1100', 35950928),
    'BRCA2': ('HGNC:1101', 35950178),
    'CCND1': ('HGNC:1582', 35963336),
    'CDKN2A': ('HGNC:1787', 35946721),
    'CREBBP': ('HGNC:2348', 35949526),
    'DIS3': ('HGNC:20604', 35948265),
    'ESR1': ('HGNC:3467', 35948983),
    'EZH2': ('HGNC:3527', 35964476),
    'KMT2D': ('HGNC:7133', 35953669),
    'KRAS': ('HGNC:6407', 35948791),
    'NOTCH1': ('HGNC:7881', 35947617),
    'NOTCH2': ('HGNC:7882', 35960756),
    'NRAS': ('HGNC:7989', 35950119),
    'NSD2': ('HGNC:12766', 35950774),
    'PALB2': ('HGNC:26144', 35949597),
    'PIK3CA': ('HGNC:8975', 35953202),
    'SF3B1': ('HGNC:10768', 35948718),
    'SMARCA4': ('HGNC:11100', 35949914),
    'TENT5C': ('HGNC:24712', 35952051),
    'TP53': ('HGNC:11998', 35948926),
    'XBP1': ('HGNC:12801', 35961405),
}
# Retired/alternate spellings seen in trial source text.
GENE_ALIASES = {
    'PALB1': 'PALB2',
    'FAM46C': 'TENT5C',
}


def canonical_gene(symbol):
    """The reviewed canonical symbol for a trial/patient spelling, or None."""
    key = str(symbol or '').strip().upper()
    key = GENE_ALIASES.get(key, key)
    return key if key in GENE_CONCEPTS else None


def gene_concept_id(symbol):
    """The reviewed OMOP Genomic gene concept id, or None for an unknown gene."""
    key = canonical_gene(symbol)
    return GENE_CONCEPTS[key][1] if key else None


def gene_hgnc_id(symbol):
    key = canonical_gene(symbol)
    return GENE_CONCEPTS[key][0] if key else None


def gene_symbols_for_concept_ids(concept_ids):
    """Legacy trial spellings for mapped ids, including reviewed aliases.

    Used only on the fallback path for a trial with no usable envelope: the
    patient's id is authoritative, and the trial's legacy symbol column still
    needs a spelling to compare against.
    """
    wanted = {str(value) for value in concept_ids}
    by_id = {}
    for symbol, (_, concept_id) in GENE_CONCEPTS.items():
        if str(concept_id) in wanted:
            by_id.setdefault(str(concept_id), set()).add(symbol.lower())
    for alias, canonical in GENE_ALIASES.items():
        concept_id = GENE_CONCEPTS[canonical][1]
        if str(concept_id) in wanted:
            by_id.setdefault(str(concept_id), set()).add(alias.lower())
    return sorted({symbol for group in by_id.values() for symbol in group})


def omop_genomics_enabled():
    """True when the trial-side gene concept ids serve genomic matching."""
    return bool(getattr(settings, 'EXACT_OMOP_GENOMICS', False))


def _concept_id(term):
    if not isinstance(term, dict) or term.get('kind') != 'genomic_subject':
        return None
    if term.get('association') != 'same_finding' or term.get('status_filter') is not None:
        return None
    feature = term.get('feature')
    if not isinstance(feature, dict):
        return None
    if feature.get('vocabulary_id') != 'OMOP Genomic':
        return None
    if feature.get('domain_id') != 'Measurement':
        return None
    concept_id = str(feature.get('concept_id') or '')
    return concept_id if concept_id.isdigit() else None


def gene_requirements(envelopes):
    """``(required, excluded)`` gene concept ids, or None when unsupported.

    Accepts the CB ``cb-trial-criteria-1`` envelope list and the flat id list
    the column started as. Returning None (missing, malformed, or carrying a
    semantic this gene-only slice cannot honour) is the caller's signal to fall
    back to the legacy symbol columns instead of guessing.
    """
    if not isinstance(envelopes, list) or not envelopes:
        return None
    required, excluded = set(), set()
    if all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in envelopes):
        ids = {str(item) for item in envelopes}
        return (ids, set()) if all(item.isdigit() for item in ids) else None
    if len(envelopes) != 1:
        # The typed field carries one envelope; several would need AND/OR
        # semantics this gene-only slice does not implement.
        return None
    for envelope in envelopes:
        if not isinstance(envelope, dict) or envelope.get('schema') != SCHEMA:
            return None
        role = envelope.get('role')
        if role not in ('required', 'excluded'):
            return None
        if envelope.get('operator') != 'any_of' or envelope.get('unknown_is_negative') is not False:
            return None
        if envelope.get('cross_axis_combination') != 'independent_exists':
            return None
        terms = envelope.get('terms')
        if not isinstance(terms, list) or not terms:
            return None
        target = required if role == 'required' else excluded
        for term in terms:
            concept_id = _concept_id(term)
            if concept_id is None:
                return None
            target.add(concept_id)
    return required, excluded


def trial_gene_requirements(trial):
    """The trial's parsed gene requirements, or None when it has no usable envelope."""
    return gene_requirements(getattr(trial, 'omop_mutation_genes_required', None))


def gene_criterion_matches(requirements, legacy_symbols, patient_keys, patient_symbols):
    """The one gene-axis rule shared by search and the per-trial matcher.

    ``requirements`` is ``gene_requirements()`` or None (fall back to the
    legacy symbol column). ``patient_keys`` are the per-finding ids/unknown
    symbols; ``patient_symbols`` are the spellings a legacy trial can compare
    (only findings without an id, plus ids mapped back to reviewed aliases).
    """
    legacy = set(legacy_symbols or ())
    if requirements is None:
        return not legacy or bool(legacy & set(patient_symbols))
    required, excluded = requirements
    keys = set(patient_keys)
    if excluded & keys:
        return False
    if required and not (required & keys):
        unknown = {key for key in keys if not key.isdigit()}
        return bool(unknown & legacy)
    return True


def legacy_gene_ids(symbols):
    """Map legacy trial symbols to concept ids; None if any symbol is unreviewed."""
    if not symbols:
        return set()
    ids = set()
    for symbol in symbols:
        concept_id = gene_concept_id(symbol)
        if concept_id is None:
            return None
        ids.add(str(concept_id))
    return ids
