"""Finding-level evaluation of the four genomic mutation criteria (#5672).

The legacy matcher flattened findings into independent global sets:

    genes = [f['gene'] for f in findings]
    interpretations = [f"{f['gene']}__{f['interpretation']}" ...]

so a BRCA1 finding of one variant could be combined with a TP53 pathogenic
finding of another. This module evaluates every criterion against one finding
at a time. Origin and interpretation are bound to the same finding; the four
axes remain independent existential requirements (``independent_exists``), not
one finding that satisfies all of them.

CB parity (cancerbot#5672 decision): ``unknown`` is reserved for a missing or
unparsed genetics payload. Once a finding object exists, a missing required
subject or qualifier is ``not_matched``. ``benign``, ``likely_benign``, ``VUS``
and ``no_mutation_detected`` are present answers; exact-code matching applies
until the SME decisions in cancerbot#5679.

Variant and origin/interpretation ids are used when the finding carries them;
otherwise the legacy per-finding tokens are the fallback. Variant concept ids
for patients are #5674, and specimen/assay/date context is #5678.
"""
from dataclasses import dataclass

from exact_matching.omop.genomics import (
    GENE_CONCEPTS, SCHEMA, gene_concept_id, gene_symbols_for_concept_ids,
)

# The same concept ids PROMOP writes into a finding's origin/interpretation
# (promop #2251). The matcher maps the read-side strings; a missing code is simply unknown
# for that qualifier and falls back to the legacy token.
GENOMIC_CONCEPTS = {
    'origin': {
        'germline': '45880308',
        'somatic': '45880309',
    },
    'interpretation': {
        'pathogenic': '45884094',
        'likely_pathogenic': '21498358',
        'benign': '45880925',
        'likely_benign': '21498359',
        'vus': '21498841',
        # LOINC LA19943-2 "Not detected", as the CB typed compiler uses it.
        # #5679 may revise the hierarchy/scope; matching stays exact.
        'no_mutation_detected': '36310150',
    },
}
_GENE_CONCEPT_IDS = {str(concept_id) for _, concept_id in GENE_CONCEPTS.values()}
_ORIGIN_CODES = {concept_id: code for code, concept_id in GENOMIC_CONCEPTS['origin'].items()}
_GENOMIC_CLASSES = frozenset({
    'Genetic Variation', 'Gene DNA Variant', 'Gene RNA Variant',
    'Gene Protein Variant', 'Structural Variant',
})

MUTATION_COLUMNS = (
    'mutation_genes_required', 'mutation_variants_required',
    'mutation_origins_required', 'mutation_interpretations_required',
    'omop_mutation_genes_required', 'omop_mutation_variants_required',
    'omop_mutation_origins_required', 'omop_mutation_interpretations_required',
)


@dataclass(frozen=True)
class GenomicTerm:
    subject_kind: str           # 'gene' | 'variant'
    subject_id: str             # feature.concept_id
    qualifier: str | None       # 'origin' | 'interpretation' | None
    answer_id: str | None
    role: str = 'required'      # 'required' | 'excluded'


def _concept_id(value):
    if isinstance(value, bool) or value in (None, ''):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return str(number) if number > 0 else None


def _feature(term):
    if not isinstance(term, dict) or term.get('kind') != 'genomic_subject':
        return None
    if term.get('association') != 'same_finding' or term.get('status_filter') is not None:
        return None
    feature = term.get('feature')
    if not isinstance(feature, dict):
        return None
    if feature.get('vocabulary_id') != 'OMOP Genomic' or feature.get('domain_id') != 'Measurement':
        return None
    if feature.get('concept_class_id') not in _GENOMIC_CLASSES:
        return None
    concept_id = _concept_id(feature.get('concept_id'))
    return (str(concept_id), feature) if concept_id else None


def _qualifier(term, field):
    qualifier = term.get('qualifier')
    if not isinstance(qualifier, dict):
        return None
    question = qualifier.get('question')
    answer = qualifier.get('answer')
    if not isinstance(question, dict) or not isinstance(answer, dict):
        return None
    source = question.get('source_value')
    answer_id = _concept_id(answer.get('concept_id'))
    if answer_id is None:
        # Stored envelopes wrap the answer as {'kind': 'concept', 'concept': …}.
        concept = answer.get('concept')
        if isinstance(concept, dict):
            answer_id = _concept_id(concept.get('concept_id'))
    if field == 'mutation_origins_required':
        if source != 'genomics:origin' or answer_id is None:
            return None
        return 'origin', answer_id
    if field == 'mutation_interpretations_required':
        if source != 'genomics:interpretation' or answer_id is None:
            return None
        return 'interpretation', answer_id
    return None


def _subject_kind(field, concept_id, concept_class):
    if field == 'mutation_genes_required':
        return 'gene'
    if field == 'mutation_variants_required':
        return 'variant'
    # Origin/interpretation may bind a gene or a variant. Gene concepts are the
    # catalog's genetic-variation class, including genes outside the 24-gene
    # crosswalk; the variant classes are the diagnostic variant concepts.
    if concept_id in _GENE_CONCEPT_IDS or concept_class == 'Genetic Variation':
        return 'gene'
    return 'variant'


def genomic_terms(envelopes, field):
    """Required/excluded terms for one mutation field, or None (legacy fallback).

    The typed envelope is the source for subject identity and (for origin /
    interpretation) the bound answer. An unsupported shape returns None so the
    caller uses the per-finding legacy tokens; the readiness command reports it.
    """
    if not isinstance(envelopes, list) or not envelopes:
        return None
    if all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in envelopes):
        if field != 'mutation_genes_required':
            return None
        ids = {_concept_id(item) for item in envelopes}
        if None in ids:
            return None
        return [GenomicTerm('gene', concept_id, None, None) for concept_id in sorted(ids)]
    if len(envelopes) != 1:
        return None
    envelope = envelopes[0]
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
    out = []
    for term in terms:
        feature = _feature(term)
        if feature is None:
            return None
        concept_id, feature_data = feature
        subject_kind = _subject_kind(field, concept_id, feature_data.get('concept_class_id'))
        qualifier = _qualifier(term, field)
        if field in ('mutation_origins_required', 'mutation_interpretations_required'):
            if qualifier is None:
                return None
        elif qualifier is not None:
            return None
        out.append(GenomicTerm(
            subject_kind, concept_id,
            qualifier[0] if qualifier else None,
            qualifier[1] if qualifier else None,
            role,
        ))
    return out


def _finding_gene_id(finding):
    concept_id = _concept_id(finding.get('gene_concept_id'))
    if concept_id:
        return concept_id
    return _concept_id(gene_concept_id(finding.get('gene')))


def _finding_gene_symbols(finding):
    concept_id = _concept_id(finding.get('gene_concept_id'))
    if concept_id:
        return set(gene_symbols_for_concept_ids([concept_id]))
    gene = str(finding.get('gene') or '').strip()
    if not gene:
        return set()
    mapped = gene_concept_id(gene)
    if mapped:
        return set(gene_symbols_for_concept_ids([mapped])) | {gene.lower()}
    return {gene.lower()}


def _finding_variant_code(finding):
    return str(finding.get('variant') or '').strip().lower()


def _finding_variant_id(finding):
    return _concept_id(finding.get('variant_concept_id'))


def _finding_origin_id(finding):
    origin = str(finding.get('origin') or '').strip().lower()
    return GENOMIC_CONCEPTS['origin'].get(origin)


def _finding_interpretation_id(finding):
    interpretation = str(finding.get('interpretation') or '').strip().lower()
    return GENOMIC_CONCEPTS['interpretation'].get(interpretation)


def _origin_tokens(finding):
    origin = str(finding.get('origin') or '').strip().lower()
    if not origin:
        return set()
    tokens = set()
    gene = str(finding.get('gene') or '').strip().lower()
    if gene:
        tokens.add(f'{origin}__{gene}')
    variant = _finding_variant_code(finding)
    if variant:
        tokens.add(f'{origin}__{variant}')
    return tokens


def _interpretation_tokens(finding):
    interpretation = str(finding.get('interpretation') or '').strip().lower()
    gene = str(finding.get('gene') or '').strip().lower()
    if not interpretation or not gene:
        return set()
    return {f'{gene}__{interpretation}'}


def _qualifier_matches(term, finding, legacy_values):
    """The bound origin/interpretation on this finding, exact or token fallback.

    A mapped patient value is authoritative: if it differs from the trial answer
    it does not fall back to its own legacy token. The token is only for a
    patient value the read-side map does not know.
    """
    if term.qualifier == 'origin':
        origin_id = _finding_origin_id(finding)
        if origin_id is not None:
            return origin_id == term.answer_id
        return bool(_origin_tokens(finding) & set(legacy_values or []))
    if term.qualifier == 'interpretation':
        interpretation_id = _finding_interpretation_id(finding)
        if interpretation_id is not None:
            return interpretation_id == term.answer_id
        return bool(_interpretation_tokens(finding) & set(legacy_values or []))
    return True


def _variant_subject_codes(term, field, legacy_values, variant_values):
    """Legacy variant codes that can stand for this term's variant subject.

    The typed variant feature has an OMOP id, not the CB code. The fallback must
    stay subject-specific:
    - the variant axis' own list is the subject set;
    - an origin term uses the origin tokens for the term's bound origin, so a
      different variant's origin token cannot satisfy it;
    - a variant-bound interpretation is not generated by CB, so fail closed.
    """
    if field == 'mutation_variants_required':
        return set(variant_values or [])
    if field == 'mutation_origins_required':
        origin = _ORIGIN_CODES.get(term.answer_id)
        if not origin:
            return set()
        prefix = f'{origin}__'
        return {
            token[len(prefix):] for token in (legacy_values or [])
            if isinstance(token, str) and token.startswith(prefix)
        }
    return set()


def _term_matches(term, finding, field, legacy_values, variant_values):
    """One finding vs one term. All conditions are on this finding."""
    if term.subject_kind == 'gene':
        if _finding_gene_id(finding) != term.subject_id:
            # A legacy finding with an unreviewed symbol may still match this
            # term only when the symbol is the term's reviewed spelling. A
            # finding that carries an id never falls back to its own text.
            gene = str(finding.get('gene') or '').strip().lower()
            term_symbols = set(gene_symbols_for_concept_ids([term.subject_id]))
            if _finding_gene_id(finding) is not None or gene not in term_symbols:
                return False
        return _qualifier_matches(term, finding, legacy_values)

    # Variant subject. Patient variant concept ids are #5674; without one, the
    # term-specific legacy code must match on this finding.
    if _finding_variant_id(finding) == term.subject_id:
        matched = True
    else:
        subject_codes = _variant_subject_codes(term, field, legacy_values, variant_values)
        matched = bool(_finding_variant_code(finding) and
                       _finding_variant_code(finding) in subject_codes)
    if not matched:
        return False
    return _qualifier_matches(term, finding, legacy_values)

def _field_matches(terms, field, legacy_values, findings, variant_values):
    legacy = list(legacy_values or [])
    if terms is None:
        # Legacy per-finding fallback. A gene uses the reviewed id/symbol map;
        # variant/origin/interpretation use the token on the same finding.
        for finding in findings:
            if field == 'mutation_genes_required':
                if _finding_gene_symbols(finding) & set(legacy):
                    return True
            elif field == 'mutation_variants_required':
                if _finding_variant_code(finding) and _finding_variant_code(finding) in set(variant_values or []):
                    return True
            elif field == 'mutation_origins_required':
                if _origin_tokens(finding) & set(legacy):
                    return True
            elif field == 'mutation_interpretations_required':
                if _interpretation_tokens(finding) & set(legacy):
                    return True
        return not legacy

    required = [term for term in terms if term.role == 'required']
    excluded = [term for term in terms if term.role == 'excluded']
    if any(_term_matches(term, finding, field, legacy, variant_values)
           for term in excluded for finding in findings):
        return False
    if not required:
        return True
    return any(_term_matches(term, finding, field, legacy, variant_values)
               for term in required for finding in findings)


def mutation_fields(trial):
    return {
        'mutation_genes_required': (
            getattr(trial, 'mutation_genes_required', []) or [],
            getattr(trial, 'omop_mutation_genes_required', []) or [],
        ),
        'mutation_variants_required': (
            getattr(trial, 'mutation_variants_required', []) or [],
            getattr(trial, 'omop_mutation_variants_required', []) or [],
        ),
        'mutation_origins_required': (
            getattr(trial, 'mutation_origins_required', []) or [],
            getattr(trial, 'omop_mutation_origins_required', []) or [],
        ),
        'mutation_interpretations_required': (
            getattr(trial, 'mutation_interpretations_required', []) or [],
            getattr(trial, 'omop_mutation_interpretations_required', []) or [],
        ),
    }


def has_mutation_requirement(trial):
    return any(legacy or typed for legacy, typed in mutation_fields(trial).values())


def genomic_mutation_state(trial, findings, is_blank):
    """``matched`` / ``not_matched`` / ``unknown`` for the four mutation axes."""
    fields = mutation_fields(trial)
    if not any(legacy or typed for legacy, typed in fields.values()):
        return 'matched'
    if is_blank:
        return 'unknown'
    findings = [finding for finding in (findings or []) if isinstance(finding, dict)]
    variant_values = fields['mutation_variants_required'][0]
    for field, (legacy, typed) in fields.items():
        if not legacy and not typed:
            continue
        terms = None
        if typed:
            terms = genomic_terms(typed, field)
            if terms is None and not legacy:
                # A non-empty typed requirement we cannot evaluate must not
                # silently become "no requirement"; the readiness audit reports
                # the shape before the flag is flipped.
                return 'not_matched'
        if not _field_matches(terms, field, legacy, findings, variant_values):
            return 'not_matched'
    return 'matched'
