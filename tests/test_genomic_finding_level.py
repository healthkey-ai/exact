"""Finding-level genomic evaluation (#5672)."""
import pytest
from django.test import override_settings

from exact_matching.omop.genomic_findings import genomic_mutation_state
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

BRCA1 = 35950928
BRCA2 = 35950178
TP53 = 35948926
VARIANT_X = 99900001          # trial-side variant concept id; patient has only the legacy code
PATHOGENIC = 45884094
BENIGN = 45880925
VUS = 21498841
NO_MUTATION = 36310150
SOMATIC = 45880309
GERMLINE = 45880308

LEGACY_VARIANT = 'c.5096g_a'


def envelope(*terms, role='required'):
    return {
        'schema': 'cb-trial-criteria-1', 'role': role, 'operator': 'any_of',
        'terms': list(terms), 'unknown_is_negative': False,
        'cross_axis_combination': 'independent_exists',
    }


def subject(concept_id, code='x', concept_class='Genetic Variation'):
    return {
        'kind': 'genomic_subject',
        'feature': {
            'domain_id': 'Measurement', 'concept_id': str(concept_id),
            'concept_code': code, 'vocabulary_id': 'OMOP Genomic',
            'concept_class_id': concept_class,
        },
        'association': 'same_finding', 'status_filter': None,
    }


def bound(concept_id, question, answer_id, code='x', concept_class='Genetic Variation'):
    term = subject(concept_id, code, concept_class)
    term['qualifier'] = {
        'question': {
            'source_value': question, 'unit': '', 'domain_id': 'Measurement',
            'concept_id': '1', 'value_kind': 'string', 'concept_code': 'x',
            'vocabulary_id': 'LOINC', 'concept_class_id': 'Lab Test',
        },
        'answer': {
            'kind': 'concept',
            'concept': {
                'domain_id': 'Meas Value', 'concept_id': str(answer_id),
                'concept_code': 'x', 'vocabulary_id': 'LOINC',
                'concept_class_id': 'Answer',
            },
        },
    }
    return term


INTERPRETATION_IDS = {
    'pathogenic': PATHOGENIC,
    'likely_pathogenic': 21498358,
    'benign': BENIGN,
    'likely_benign': 21498359,
    'vus': VUS,
    'no_mutation_detected': NO_MUTATION,
}


def trial(**fields):
    genes = fields.pop('genes', [])
    variants = fields.pop('variants', [])
    origins = fields.pop('origins', [])
    interpretations = fields.pop('interpretations', [])
    typed = fields.pop('typed', True)
    data = {
        'mutation_genes_required': genes,
        'mutation_variants_required': variants,
        'mutation_origins_required': origins,
        'mutation_interpretations_required': interpretations,
    }
    if typed:
        if genes:
            data['omop_mutation_genes_required'] = [envelope(*[subject(BRCA1 if g == 'brca1' else BRCA2 if g == 'brca2' else TP53) for g in genes])]
        if variants:
            data['omop_mutation_variants_required'] = [
                envelope(subject(VARIANT_X, concept_class='Gene RNA Variant'))
            ]
        if origins:
            data['omop_mutation_origins_required'] = [
                envelope(*[bound(BRCA1, 'genomics:origin', SOMATIC)])
            ]
        if interpretations:
            answer = INTERPRETATION_IDS[interpretations[0].split('__', 1)[1]]
            data['omop_mutation_interpretations_required'] = [
                envelope(*[bound(BRCA1, 'genomics:interpretation', answer)])
            ]
    data.update(fields)
    return TrialFactory(**data)


def verdict(trial_obj, *findings):
    patient = PatientInfo(disease='breast cancer', genetic_mutations=list(findings))
    return UserToTrialAttrMatcher(trial_obj, patient).attr_match_status('genetic_mutations')


def state(trial_obj, findings, is_blank=False):
    return genomic_mutation_state(trial_obj, findings, is_blank)


# ── the cross-finding bug ────────────────────────────────────────────

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_gene_and_variant_axes_are_independent_exists():
    """CB's typed contract keeps the four axes independent (#5672 decision)."""
    t = trial(genes=['brca1'], variants=[LEGACY_VARIANT])
    same = {'gene': 'brca1', 'variant': LEGACY_VARIANT}
    split = [{'gene': 'brca1'}, {'gene': 'tp53', 'variant': LEGACY_VARIANT}]
    assert state(t, [same]) == 'matched'
    assert state(t, split) == 'matched'
    assert verdict(t, *split) == 'matched'


@override_settings(EXACT_OMOP_GENOMICS=False)
def test_the_legacy_path_keeps_the_same_independent_axis_result():
    t = trial(genes=['brca1'], variants=[LEGACY_VARIANT], typed=False)
    split = [{'gene': 'brca1'}, {'gene': 'tp53', 'variant': LEGACY_VARIANT}]
    assert verdict(t, *split) == 'matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_gene_and_interpretation_are_bound_to_the_same_finding():
    t = trial(genes=['brca1'], interpretations=['brca1__pathogenic'])
    assert state(t, [{'gene': 'brca1', 'interpretation': 'pathogenic'}]) == 'matched'
    assert state(t, [
        {'gene': 'brca1'},
        {'gene': 'tp53', 'interpretation': 'pathogenic'},
    ]) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_origin_is_bound_to_the_same_finding():
    t = trial(origins=['somatic__brca1'])
    assert state(t, [{'gene': 'brca1', 'origin': 'somatic'}]) == 'matched'
    assert state(t, [
        {'gene': 'brca1', 'origin': 'germline'},
        {'gene': 'tp53', 'origin': 'somatic'},
    ]) == 'not_matched'


# ── CB parity: missing qualifier, exact interpretation codes ─────────

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_empty_payload_is_unknown_but_a_present_finding_without_qualifier_is_not_matched():
    t = trial(genes=['brca1'], interpretations=['brca1__vus'])
    assert state(t, [], is_blank=True) == 'unknown'
    assert state(t, [{'gene': 'brca1'}]) == 'not_matched'
    assert state(t, [{'gene': 'brca1', 'interpretation': 'vus'}]) == 'matched'


@pytest.mark.parametrize('interpretation,expected', [
    ('pathogenic', 'matched'),
    ('likely_pathogenic', 'not_matched'),
    ('benign', 'not_matched'),
    ('likely_benign', 'not_matched'),
    ('vus', 'not_matched'),
    ('no_mutation_detected', 'not_matched'),
])
@override_settings(EXACT_OMOP_GENOMICS=True)
def test_pathogenic_requirement_matches_only_the_exact_code(interpretation, expected):
    t = trial(genes=['brca1'], interpretations=['brca1__pathogenic'])
    assert state(t, [{'gene': 'brca1', 'interpretation': interpretation}]) == expected


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_no_mutation_detected_is_a_present_answer_with_its_own_requirement():
    t = trial(genes=['brca1'], interpretations=['brca1__no_mutation_detected'])
    assert state(t, [{'gene': 'brca1', 'interpretation': 'no_mutation_detected'}]) == 'matched'
    assert state(t, [{'gene': 'brca1', 'interpretation': 'pathogenic'}]) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_no_mutation_detected_finding_still_satisfies_a_gene_only_requirement():
    t = trial(genes=['brca1'])
    assert state(t, [{'gene': 'brca1', 'interpretation': 'no_mutation_detected'}]) == 'matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_status_is_not_a_positive_status_filter():
    t = trial(genes=['brca1'])
    assert state(t, [{'gene': 'brca1', 'status': 'absent'}]) == 'matched'


# ── independent axes and multi-finding ──────────────────────────────

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_each_axis_is_an_independent_exists_not_one_universal_finding():
    t = trial(genes=['brca1', 'brca2'])
    assert state(t, [{'gene': 'brca1'}, {'gene': 'brca2'}]) == 'matched'
    assert state(t, [{'gene': 'brca1'}]) == 'matched'
    assert state(t, [{'gene': 'tp53'}]) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_two_findings_of_the_same_gene_can_be_combined_for_the_interpretation_axis():
    t = trial(genes=['brca1'], interpretations=['brca1__pathogenic'])
    findings = [
        {'gene': 'brca1', 'interpretation': 'benign'},
        {'gene': 'brca1', 'interpretation': 'pathogenic'},
    ]
    assert state(t, findings) == 'matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_no_mutation_requirement_matches_any_finding():
    t = trial()
    assert state(t, [{'gene': 'brca1'}]) == 'matched'
    assert state(t, [], is_blank=True) == 'matched'


# ── search parity ───────────────────────────────────────────────────

def search(findings):
    return set(Trial.objects.eligible_for_genetic_mutations(findings).values_list('id', flat=True))


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_matches_the_matcher_when_axes_are_independent():
    t = trial(genes=['brca1'], variants=[LEGACY_VARIANT])
    split = [{'gene': 'brca1'}, {'gene': 'tp53', 'variant': LEGACY_VARIANT}]
    assert verdict(t, *split) == 'matched'
    assert t.id in search(split)


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_matches_the_matcher_for_the_missing_qualifier_case():
    t = trial(genes=['brca1'], interpretations=['brca1__vus'])
    findings = [{'gene': 'brca1'}]
    assert verdict(t, *findings) == 'not_matched'
    assert t.id not in search(findings)
    assert t.id in search([{'gene': 'brca1', 'interpretation': 'vus'}])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_search_keeps_a_trial_without_mutation_requirements():
    t = trial()
    assert t.id in search([{'gene': 'brca1'}])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_empty_payload_stays_unknown_and_the_hard_filter_is_skipped_by_the_caller():
    t = trial(genes=['brca1'])
    # `eligible_for_genetic_mutations` returns self for []: the search caller
    # skips it for a blank attr and classifies the trial as potential instead.
    assert t.id in search([])
    assert state(t, [], is_blank=True) == 'unknown'


# ── counts / potential agree with the hard filter ────────────────────

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_eligible_count_agrees_with_the_finding_level_filter():
    matched = trial(genes=['brca1'], interpretations=['brca1__pathogenic'])
    excluded = trial(genes=['brca2'])
    patient = PatientInfo(
        disease='breast cancer',
        genetic_mutations=[{'gene': 'brca1', 'interpretation': 'pathogenic'}],
    )
    qs = (
        Trial.objects.eligible_for_genetic_mutations(patient.genetic_mutations)
        .with_potential_attrs_count(patient, search_type='eligible')
    )
    ids = set(qs.values_list('id', flat=True))
    assert matched.id in ids
    assert excluded.id not in ids


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_blank_payload_keeps_a_mutation_trial_potential():
    t = trial(genes=['brca1'])
    patient = PatientInfo(disease='breast cancer', genetic_mutations=[])
    qs = Trial.objects.all().with_potential_attrs_count(patient, search_type='potential')
    assert t.id in set(qs.values_list('id', flat=True))


# ── shadow compare command ──────────────────────────────────────────

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_shadow_compare_reports_a_changed_verdict(tmp_path):
    import json
    from io import StringIO

    from django.core.management import call_command

    t = trial(genes=['brca1'], typed=False)
    findings = [{'gene': 'brca1', 'gene_concept_id': TP53}]
    path = tmp_path / 'findings.json'
    path.write_text(json.dumps(findings))

    out = StringIO()
    call_command('shadow_compare_genomic_matching', '--patient-json', str(path),
                 '--json', stdout=out)
    payload = json.loads(out.getvalue())
    assert payload['counts']['changed'] >= 1
    assert any(row['trial_id'] == t.id and row['legacy'] == 'matched'
               and row['finding_level'] == 'not_matched'
               for row in payload['changed'])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_blank_payload_keeps_a_typed_only_mutation_trial_potential():
    t = TrialFactory(
        mutation_genes_required=[],
        omop_mutation_genes_required=[envelope(subject(BRCA1))],
    )
    patient = PatientInfo(disease='breast cancer', genetic_mutations=[])
    qs = Trial.objects.all().with_potential_attrs_count(patient, search_type='potential')
    assert t.id in set(qs.values_list('id', flat=True))


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_typed_only_gene_is_evaluated_without_a_legacy_mirror():
    t = TrialFactory(
        mutation_genes_required=[],
        omop_mutation_genes_required=[envelope(subject(BRCA1))],
    )
    finding = {'gene': 'brca1', 'gene_concept_id': BRCA1}
    assert state(t, [finding]) == 'matched'
    assert t.id in search([finding])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_typed_only_interpretation_is_evaluated_without_a_legacy_mirror():
    t = TrialFactory(
        mutation_interpretations_required=[],
        omop_mutation_interpretations_required=[
            envelope(bound(BRCA1, 'genomics:interpretation', PATHOGENIC))
        ],
    )
    finding = {'gene': 'brca1', 'interpretation': 'pathogenic'}
    assert state(t, [finding]) == 'matched'
    assert t.id in search([finding])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_mapped_qualifier_is_not_rescued_by_a_contradictory_legacy_token():
    origin_trial = TrialFactory(
        mutation_origins_required=['germline__brca1'],
        omop_mutation_origins_required=[
            envelope(bound(BRCA1, 'genomics:origin', SOMATIC))
        ],
    )
    assert state(origin_trial, [{'gene': 'brca1', 'origin': 'germline'}]) == 'not_matched'

    interpretation_trial = TrialFactory(
        mutation_interpretations_required=['brca1__benign'],
        omop_mutation_interpretations_required=[
            envelope(bound(BRCA1, 'genomics:interpretation', PATHOGENIC))
        ],
    )
    assert state(interpretation_trial,
                 [{'gene': 'brca1', 'interpretation': 'benign'}]) == 'not_matched'


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_gene_outside_the_24_gene_crosswalk_uses_the_genetic_variation_class():
    non_catalog_gene = 123456789
    t = TrialFactory(
        mutation_origins_required=['somatic__not_in_the_catalog'],
        omop_mutation_origins_required=[
            envelope({
                'kind': 'genomic_subject',
                'feature': {
                    'domain_id': 'Measurement', 'concept_id': str(non_catalog_gene),
                    'concept_code': 'x', 'vocabulary_id': 'OMOP Genomic',
                    'concept_class_id': 'Genetic Variation',
                },
                'association': 'same_finding', 'status_filter': None,
                'qualifier': {
                    'question': {'source_value': 'genomics:origin'},
                    'answer': {'concept_id': str(SOMATIC)},
                },
            })
        ],
    )
    finding = {'gene': 'not_in_the_catalog', 'gene_concept_id': non_catalog_gene, 'origin': 'somatic'}
    assert state(t, [finding]) == 'matched'


# ── variant-subject fallback must stay term-specific (review findings) ─

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_variant_origin_term_cannot_be_satisfied_by_a_different_variant_from_the_variant_axis():
    v1, v2 = 90000001, 90000002
    t = TrialFactory(
        mutation_variants_required=['v1', 'v2'],
        omop_mutation_variants_required=[
            envelope(subject(v1, concept_class='Gene RNA Variant'),
                     subject(v2, concept_class='Gene RNA Variant'))
        ],
        mutation_origins_required=['somatic__v1'],
        omop_mutation_origins_required=[
            envelope(bound(v1, 'genomics:origin', SOMATIC, concept_class='Gene RNA Variant'))
        ],
    )
    findings = [
        {'variant': 'v1', 'origin': 'germline'},
        {'variant': 'v2', 'origin': 'somatic'},
    ]
    assert state(t, findings) == 'not_matched'
    assert t.id not in search(findings)


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_variant_origin_term_uses_its_own_origin_token_without_a_variant_axis():
    v1 = 90000003
    t = TrialFactory(
        mutation_variants_required=[],
        mutation_origins_required=['somatic__v1'],
        omop_mutation_origins_required=[
            envelope(bound(v1, 'genomics:origin', SOMATIC, concept_class='Gene RNA Variant'))
        ],
    )
    findings = [{'variant': 'v1', 'origin': 'somatic'}]
    assert state(t, findings) == 'matched'
    assert t.id in search(findings)


# ── mapped id is authoritative; unknown symbols still have a term fallback ─

@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_contradictory_variant_concept_id_is_not_rescued_by_the_legacy_code():
    v1, v2 = 90000011, 90000012
    t = TrialFactory(
        mutation_origins_required=['somatic__v1'],
        omop_mutation_origins_required=[
            envelope(bound(v1, 'genomics:origin', SOMATIC, concept_class='Gene RNA Variant'))
        ],
    )
    finding = {'variant': 'v1', 'variant_concept_id': v2, 'origin': 'somatic'}
    assert state(t, [finding]) == 'not_matched'
    assert t.id not in search([finding])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_gene_outside_the_catalog_keeps_its_symbol_fallback_on_the_gene_axis():
    non_catalog_gene = 123456780
    t = TrialFactory(
        mutation_genes_required=['outside'],
        omop_mutation_genes_required=[envelope(subject(non_catalog_gene))],
    )
    finding = {'gene': 'outside'}
    assert state(t, [finding]) == 'matched'
    assert t.id in search([finding])


@override_settings(EXACT_OMOP_GENOMICS=True)
def test_a_gene_outside_the_catalog_keeps_its_symbol_fallback_on_a_bound_origin():
    non_catalog_gene = 123456781
    t = TrialFactory(
        mutation_origins_required=['somatic__outside'],
        omop_mutation_origins_required=[
            envelope(bound(non_catalog_gene, 'genomics:origin', SOMATIC))
        ],
    )
    finding = {'gene': 'outside', 'origin': 'somatic'}
    assert state(t, [finding]) == 'matched'
    assert t.id in search([finding])
