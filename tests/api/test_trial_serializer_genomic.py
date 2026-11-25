"""The search row carries the genomic requirements the card renders (#377).

The detail page already showed Mutation Genes / Mutation Variants; the list
serializer did not, so a genomic-filtered search could not say why those
trials matched without opening each one.
"""
import pytest

from trials.api.trials_serializers import TrialSerializer
from tests.factories import TrialFactory


@pytest.mark.django_db
def test_list_serializer_carries_genomic_requirements():
    trial = TrialFactory(
        mutation_genes_required=['brca1', 'brca2'],
        mutation_variants_required=['c.524g_a_(r175h)'],
    )
    # `get_queryset` annotates these on a real list; the unit row has to carry
    # them or the serializer's existing fields fail before the new ones.
    trial.goodness_score = 0
    trial.match_score = 0

    data = TrialSerializer(trial, context={}).data

    assert data['mutationGenesRequired'] == ['brca1', 'brca2']
    assert data['mutationVariantsRequired'] == ['c.524g_a_(r175h)']


@pytest.mark.django_db
def test_list_serializer_keeps_an_empty_requirement_a_list():
    trial = TrialFactory(mutation_genes_required=[], mutation_variants_required=[])
    trial.goodness_score = 0
    trial.match_score = 0

    data = TrialSerializer(trial, context={}).data

    assert data['mutationGenesRequired'] == []
    assert data['mutationVariantsRequired'] == []
