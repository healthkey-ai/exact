"""Gene concept matching for genomic criteria (#5673)."""
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from exact_matching.omop.genomics import (
    GENE_CONCEPTS, GENE_ALIASES, canonical_gene, gene_concept_id,
    gene_requirements, gene_symbols_for_concept_ids, legacy_gene_ids,
)
from exact_matching.patient_info.genetic_mutations import GeneticMutations
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

BRCA1 = '35950928'
BRCA2 = '35950178'
TP53 = '35948926'


def gene_envelope(*ids, role='required', **overrides):
    terms = [{
        'kind': 'genomic_subject',
        'feature': {
            'domain_id': 'Measurement', 'concept_id': str(concept_id),
            'concept_code': 'x', 'vocabulary_id': 'OMOP Genomic',
            'concept_class_id': 'Genetic Variation',
        },
        'association': 'same_finding', 'status_filter': None,
    } for concept_id in ids]
    envelope = {
        'schema': 'cb-trial-criteria-1', 'role': role, 'operator': 'any_of',
        'terms': terms, 'unknown_is_negative': False,
        'cross_axis_combination': 'independent_exists',
    }
    envelope.update(overrides)
    return envelope


def trial_with_genes(*ids, legacy=None):
    return TrialFactory(
        mutation_genes_required=list(legacy or ['brca1']),
        omop_mutation_genes_required=[gene_envelope(*ids)],
    )


def verdict(trial, *findings):
    patient = PatientInfo(disease='breast cancer', genetic_mutations=list(findings))
    return UserToTrialAttrMatcher(trial, patient).attr_match_status('genetic_mutations')


class TestGeneMap:
    def test_twenty_four_reviewed_genes_with_unique_concepts(self):
        assert len(GENE_CONCEPTS) == 24
        assert len({concept for _, concept in GENE_CONCEPTS.values()}) == 24
        assert all(hgnc.startswith('HGNC:') for hgnc, _ in GENE_CONCEPTS.values())
        assert GENE_CONCEPTS['BRCA1'] == ('HGNC:1100', 35950928)
        assert GENE_CONCEPTS['TENT5C'] == ('HGNC:24712', 35952051)

    @pytest.mark.parametrize('symbol,expected', [
        ('BRCA1', 35950928), ('brca1', 35950928),
        ('PALB1', 35949597), ('PALB2', 35949597),
        ('FAM46C', 35952051), ('TENT5C', 35952051),
        ('NOT-A-GENE', None), (None, None),
    ])
    def test_aliases_resolve_to_one_id(self, symbol, expected):
        assert gene_concept_id(symbol) == expected

    def test_canonical_symbols_are_not_aliased_away(self):
        assert canonical_gene('brca1') == 'BRCA1'
        assert GENE_ALIASES == {'PALB1': 'PALB2', 'FAM46C': 'TENT5C'}

    def test_mapped_ids_map_back_to_all_reviewed_spellings(self):
        assert gene_symbols_for_concept_ids([35949597]) == ['palb1', 'palb2']
        assert gene_symbols_for_concept_ids([35952051]) == ['fam46c', 'tent5c']
        assert gene_symbols_for_concept_ids([999]) == []


class TestGeneRequirements:
    def test_flat_list_is_still_accepted(self):
        assert gene_requirements([BRCA1, BRCA2]) == ({BRCA1, BRCA2}, set())

    def test_envelope_required_and_excluded(self):
        assert gene_requirements([gene_envelope(BRCA1, BRCA2)]) == ({BRCA1, BRCA2}, set())
        assert gene_requirements([gene_envelope(TP53, role='excluded')]) == (set(), {TP53})

    @pytest.mark.parametrize('envelopes', [
        None, [], [gene_envelope(BRCA1), gene_envelope(BRCA2)],
        [gene_envelope(BRCA1, schema='other')],
        [gene_envelope(BRCA1, operator='at_least')],
        [gene_envelope(BRCA1, unknown_is_negative=True)],
        [gene_envelope(BRCA1, cross_axis_combination=None)],
        [gene_envelope(BRCA1, terms=[])],
        [{'schema': 'cb-trial-criteria-1', 'role': 'required', 'operator': 'any_of',
          'terms': [{'kind': 'finding', 'feature': {'concept_id': BRCA1},
                     'association': 'same_finding', 'status_filter': None}],
          'unknown_is_negative': False, 'cross_axis_combination': 'independent_exists'}],
        [{'schema': 'cb-trial-criteria-1', 'role': 'required', 'operator': 'any_of',
          'terms': [{'kind': 'genomic_subject', 'feature': {'concept_id': BRCA1},
                     'association': 'same_finding', 'status_filter': 'present'}],
          'unknown_is_negative': False, 'cross_axis_combination': 'independent_exists'}],
        [{'schema': 'cb-trial-criteria-1', 'role': 'required', 'operator': 'any_of',
          'terms': [{'kind': 'genomic_subject',
                     'feature': {'concept_id': BRCA1, 'vocabulary_id': 'SNOMED',
                                 'domain_id': 'Measurement'},
                     'association': 'same_finding', 'status_filter': None}],
          'unknown_is_negative': False, 'cross_axis_combination': 'independent_exists'}],
    ])
    def test_unsupported_shapes_fall_back(self, envelopes):
        assert gene_requirements(envelopes) is None

    def test_legacy_symbols_map_all_or_nothing(self):
        assert legacy_gene_ids(['brca1', 'PALB1']) == {BRCA1, '35949597'}
        assert legacy_gene_ids(['brca1', 'not-a-gene']) is None
        assert legacy_gene_ids([]) == set()


class TestPatientKeys:
    def test_id_wins_over_contradictory_symbol(self):
        keys = GeneticMutations.mutation_gene_keys([{
            'gene': 'tp53', 'gene_concept_id': int(TP53),
        }])
        assert keys == [TP53]

    def test_legacy_symbol_is_mapped(self):
        assert GeneticMutations.mutation_gene_keys([{'gene': 'brca1'}]) == [BRCA1]
        assert GeneticMutations.mutation_gene_keys([{'gene': 'palb1'}]) == ['35949597']

    def test_unknown_symbol_stays_a_symbol(self):
        assert GeneticMutations.mutation_gene_keys([{'gene': 'not-a-gene'}]) == ['not-a-gene']

    def test_concept_ids_are_deduped(self):
        assert set(GeneticMutations.mutation_gene_concept_ids([
            {'gene_concept_id': int(BRCA2)}, {'gene_concept_id': int(BRCA1)},
            {'gene_concept_id': int(BRCA2)},
        ])) == {BRCA1, BRCA2}


@override_settings(EXACT_OMOP_GENOMICS=False)
def test_matcher_keeps_legacy_symbols_when_the_flag_is_off():
    trial = trial_with_genes(BRCA1)
    assert verdict(trial, {'gene': 'brca1'}) == 'matched'
    assert verdict(trial, {'gene': 'tp53'}) == 'not_matched'
    # The legacy path ignores the id entirely, as it did before the flag.
    assert verdict(trial, {'gene': 'tp53', 'gene_concept_id': int(BRCA1)}) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_matcher_compares_gene_concept_ids_when_the_flag_is_on():
    trial = trial_with_genes(BRCA1)
    assert verdict(trial, {'gene': 'brca1', 'gene_concept_id': int(BRCA1)}) == 'matched'
    assert verdict(trial, {'gene': 'brca1', 'gene_concept_id': int(TP53)}) == 'not_matched'
    assert verdict(trial, {'gene': 'brca1'}) == 'matched'  # legacy finding fallback
    # A mapped finding never contributes its contradictory symbol.
    assert verdict(trial, {'gene': 'brca1', 'gene_concept_id': 999}) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_matcher_falls_back_when_the_trial_has_no_usable_envelope():
    trial = TrialFactory(mutation_genes_required=['brca1'])
    assert verdict(trial, {'gene': 'brca1', 'gene_concept_id': int(BRCA1)}) == 'matched'
    assert verdict(trial, {'gene_concept_id': int(BRCA1)}) == 'matched'
    assert verdict(trial, {'gene': 'tp53', 'gene_concept_id': int(TP53)}) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_matcher_honours_an_excluded_gene_and_unknown_state():
    excluded = TrialFactory(
        mutation_genes_required=['brca1'],
        omop_mutation_genes_required=[gene_envelope(TP53, role='excluded')],
    )
    assert verdict(excluded, {'gene': 'brca1', 'gene_concept_id': int(TP53)}) == 'not_matched'
    assert verdict(excluded, {'gene': 'brca1', 'gene_concept_id': int(BRCA1)}) == 'matched'
    assert verdict(excluded) == 'unknown'


@override_settings(EXACT_OMOP_GENOMICS=False)
def test_search_uses_legacy_symbols_when_the_flag_is_off():
    trial = trial_with_genes(BRCA1)
    ids = set(Trial.objects.eligible_for_genetic_mutations(
        [{'gene': 'brca1'}]).values_list('id', flat=True))
    assert ids == {trial.id}


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_uses_the_typed_gene_ids_when_the_flag_is_on():
    trial = trial_with_genes(BRCA1)
    ids = set(Trial.objects.eligible_for_genetic_mutations(
        [{'gene': 'brca1', 'gene_concept_id': int(BRCA1)}]).values_list('id', flat=True))
    assert ids == {trial.id}
    assert not Trial.objects.eligible_for_genetic_mutations(
        [{'gene': 'brca1', 'gene_concept_id': int(TP53)}]).exists()


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_keeps_a_no_envelope_trial_on_the_legacy_path():
    legacy = TrialFactory(mutation_genes_required=['brca1'])
    found = set(Trial.objects.eligible_for_genetic_mutations(
        [{'gene': 'brca1', 'gene_concept_id': int(BRCA1)}]).values_list('id', flat=True))
    assert found == {legacy.id}
    # A mapped finding does not rescue its contradictory symbol.
    assert not Trial.objects.eligible_for_genetic_mutations(
        [{'gene': 'brca1', 'gene_concept_id': 999}]).exists()


class TestReadinessCommand:
    def _run(self):
        out = StringIO()
        call_command('check_omop_genomics_readiness', stdout=out)
        return out.getvalue()

    def test_ok_envelope(self):
        trial_with_genes(BRCA1)
        output = self._run()
        assert 'ok: 1' in output
        assert 'All trial gene envelopes match' in output

    def test_missing_envelope_fails(self):
        TrialFactory(mutation_genes_required=['brca1'])
        with pytest.raises(CommandError, match='not ready'):
            self._run()

    def test_mismatch_and_orphan_fail(self):
        trial_with_genes(TP53)  # legacy brca1 vs TP53 envelope
        TrialFactory(mutation_genes_required=[], omop_mutation_genes_required=[gene_envelope(BRCA1)])
        with pytest.raises(CommandError, match='not ready'):
            self._run()

    def test_unmapped_symbol_fails(self):
        TrialFactory(mutation_genes_required=['not-a-gene'])
        with pytest.raises(CommandError, match='not ready'):
            self._run()


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_contradictory_id_is_not_rescued_by_its_symbol_on_the_legacy_path():
    """A trial without an envelope must still respect the finding's id."""
    trial = TrialFactory(mutation_genes_required=['brca1'])
    finding = {'gene': 'brca1', 'gene_concept_id': int(TP53)}
    assert verdict(trial, finding) == 'not_matched'
    assert not Trial.objects.eligible_for_genetic_mutations(
        [finding]).filter(id=trial.id).exists()


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_excluded_envelope_search_and_matcher_agree():
    trial = TrialFactory(
        mutation_genes_required=['brca1'],
        omop_mutation_genes_required=[gene_envelope(TP53, role='excluded')],
    )
    excluded = [{'gene': 'tp53', 'gene_concept_id': int(TP53)}]
    allowed = [{'gene': 'brca1', 'gene_concept_id': int(BRCA1)}]
    assert verdict(trial, *excluded) == 'not_matched'
    assert not Trial.objects.eligible_for_genetic_mutations(
        excluded).filter(id=trial.id).exists()
    assert verdict(trial, *allowed) == 'matched'
    assert Trial.objects.eligible_for_genetic_mutations(
        allowed).filter(id=trial.id).exists()


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_unsupported_envelope_search_falls_back_like_the_matcher():
    trial = TrialFactory(
        mutation_genes_required=['tp53'],
        omop_mutation_genes_required=[{**gene_envelope(BRCA1), 'schema': 'other'}],
    )
    finding = [{'gene': 'brca1', 'gene_concept_id': int(BRCA1)}]
    assert verdict(trial, *finding) == 'not_matched'
    assert not Trial.objects.eligible_for_genetic_mutations(
        finding).filter(id=trial.id).exists()


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_accepts_a_flat_id_list():
    trial = TrialFactory(
        mutation_genes_required=['brca1'], omop_mutation_genes_required=[BRCA1],
    )
    finding = [{'gene_concept_id': int(BRCA1)}]
    assert verdict(trial, *finding) == 'matched'
    assert Trial.objects.eligible_for_genetic_mutations(
        finding).filter(id=trial.id).exists()


class TestReadinessCommandMalformed:
    def test_malformed_orphan_envelope_fails(self):
        TrialFactory(mutation_genes_required=[], omop_mutation_genes_required={'bad': 'object'})
        with pytest.raises(CommandError, match='not ready'):
            call_command('check_omop_genomics_readiness', stdout=StringIO())
