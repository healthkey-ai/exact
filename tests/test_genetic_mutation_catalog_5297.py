"""#5297: the seeded examples match the SME's validated list (2026-11-13).

Ported from CancerBot's catalog test. Literal copy of the reviewer's
spreadsheet so a catalog edit cannot silently drift from the approved examples.
"""
from collections import Counter

import pytest
from django.core.cache import cache

from trials.models import MutationCode, MutationGene
from trials.services.genetic_mutation_catalog import (
    V422DEL_CODE,
    V422DEL_TITLE,
    code,
    label_for_stored_code,
)
from trials.services.loaders.load_genetic_mutations import LoadGeneticMutations
from trials.services.loaders.reconcile_genetic_mutations import (
    reconcile_mutation_catalog,
)
from trials.services.value_options import ValueOptions

APPROVED = {
    'TP53': (
        ('c.329G>A', 'R110H'),
        ('c.329G>T', 'R110L'),
        ('c.332T>A', 'L111Q'),
        ('c.332T>G', 'L111R'),
        ('c.404G>A', 'C135Y'),
        ('c.404G>T', 'C135F'),
        ('c.451C>T', 'P151S'),
        ('c.461G>T', 'G154V'),
        ('c.469G>T', 'V157F'),
        ('c.473G>A', 'R158H'),
        ('c.473G>T', 'R158L'),
        ('c.476C>T', 'A159V'),
        ('c.487T>A', 'Y163N'),
        ('c.488A>G', 'Y163C'),
        ('c.499C>A', 'Q167K'),
        ('c.524G>A', 'R175H'),
        ('c.535C>T', 'H179Y'),
        ('c.577C>T', 'H193Y'),
        ('c.584T>C', 'I195T'),
        ('c.586C>T', 'R196*'),
        ('c.587G>A', 'R196Q'),
        ('c.638G>A', 'R213Q'),
        ('c.637C>T', 'R213*'),
        ('c.638G>T', 'R213L'),
        ('c.659A>G', 'Y220C'),
        ('c.701A>G', 'Y234C'),
        ('c.713G>T', 'C238F'),
        ('c.733G>A', 'G245S'),
        ('c.742C>T', 'R248W'),
        ('c.743G>A', 'R248Q'),
        ('c.747G>T', 'R249S'),
        ('c.797G>T', 'G266V'),
        ('c.818G>A', 'R273H'),
        ('c.817C>T', 'R273C'),
        ('c.818G>T', 'R273L'),
        ('c.833C>T', 'P278L'),
        ('c.841G>C', 'D281H'),
        ('c.844C>T', 'R282W'),
        ('c.844C>G', 'R282G'),
        ('c.853G>A', 'E285K'),
        ('c.874A>G', 'K292E'),
        ('c.916C>T', 'R306*'),
        ('c.917G>A', 'R306Q'),
    ),
    'PIK3CA': (
        ('c.1624G>A', 'E542K'),
        ('c.1625A>G', 'E542G'),
        ('c.1633G>A', 'E545K'),
        ('c.1633G>C', 'E545Q'),
        ('c.1637A>C', 'Q546P'),
        ('c.1637A>G', 'Q546R'),
        ('c.1636C>A', 'Q546K'),
        ('c.3140A>C', 'H1047P'),
        ('c.3140A>G', 'H1047R'),
        ('c.3140A>T', 'H1047L'),
        ('c.3142C>T', 'H1048Y'),
        ('c.3143A>G', 'H1048R'),
        ('c.3144T>G', 'H1048Q'),
    ),
    'ESR1': (
        ('c.908A>G', 'K303R'),
        ('c.1138G>C', 'E380Q'),
        ('c.1265_1267del', 'V422del'),
        ('c.1387T>C', 'S463P'),
        ('c.1604C>A', 'P535H'),
        ('c.1607T>A', 'L536H'),
        ('c.1607T>C', 'L536P'),
        ('c.1607T>G', 'L536R'),
        ('c.1607_1608delinsAG', 'L536Q'),
        ('c.1609T>A', 'Y537N'),
        ('c.1610A>C', 'Y537S'),
        ('c.1610A>G', 'Y537C'),
        ('c.1613A>G', 'D538G'),
    ),
}

#: Present in the catalog, absent from the validated list, waiting for an answer.
PIK3CA_PENDING_ANSWER = (
    ('c.3145G>A', 'G1049S'),
    ('c.3145G>C', 'G1049R'),
)


def _example(title):
    cDNA, parenthesised = title.split(' ', 1)
    protein = parenthesised.strip()[1:-1]
    return cDNA, protein.removeprefix('p.')


def _seeded(gene):
    return Counter(
        _example(title)
        for title in MutationCode.objects.filter(
            gene__code=gene.lower(),
        ).values_list('title', flat=True)
    )


def _reseed():
    MutationCode.objects.filter(
        gene__code__in=('tp53', 'pik3ca', 'esr1')
    ).delete()
    LoadGeneticMutations().load_all()


@pytest.mark.django_db
@pytest.mark.parametrize('gene', sorted(APPROVED))
def test_seeded_examples_match_the_validated_list(gene):
    _reseed()
    expected = Counter(APPROVED[gene])
    if gene == 'PIK3CA':
        expected.update(PIK3CA_PENDING_ANSWER)

    # Counter, not set: a second row for the same variant must fail too.
    assert _seeded(gene) == expected


@pytest.mark.django_db
def test_v422del_keeps_the_stored_code_and_shows_the_corrected_label():
    _reseed()
    row = MutationCode.objects.get(code=V422DEL_CODE)

    assert row.title == V422DEL_TITLE
    assert code(V422DEL_TITLE) == row.code


@pytest.mark.django_db
def test_cached_options_show_the_corrected_labels():
    """The deploy path reads the cached blob; a missing bump must fail here."""
    _reseed()
    cache.clear()
    options = ValueOptions().all_options()['geneticMutationVariants']['options']
    labels = {option['value']: option['label'] for option in options}

    assert labels[V422DEL_CODE] == V422DEL_TITLE
    assert labels['c.1607t_c_(l536p)'] == 'c.1607T>C (L536P)'
    assert labels['c.1607t_g_(l536r)'] == 'c.1607T>G (L536R)'


@pytest.mark.django_db
def test_rerunning_the_loader_does_not_duplicate_v422del():
    _reseed()
    LoadGeneticMutations().load_all()

    rows = MutationCode.objects.filter(
        gene__code='esr1', title__contains='V422del',
    ).values_list('code', 'title')

    assert list(rows) == [(V422DEL_CODE, V422DEL_TITLE)]


@pytest.mark.django_db
def test_loader_converges_a_competing_v422del_code():
    """A row holding the label under another code must not split identity."""
    gene = MutationGene.objects.get(code='esr1')
    MutationCode.objects.filter(code=V422DEL_CODE).delete()
    MutationCode.objects.filter(title=V422DEL_TITLE).delete()
    MutationCode.objects.create(
        code='c.1265_1267del_(v422del)',
        title=V422DEL_TITLE,
        gene=gene,
    )

    LoadGeneticMutations().load_all()

    assert MutationCode.objects.filter(
        code=V422DEL_CODE, title=V422DEL_TITLE,
    ).exists()
    assert not MutationCode.objects.filter(
        code='c.1265_1267del_(v422del)',
    ).exists()
    assert label_for_stored_code('c.1265_1267del_(v422del)') == V422DEL_TITLE


@pytest.mark.django_db
def test_reseeding_removes_rows_from_a_pre_correction_database():
    """Upsert alone would leave obsolete options selectable on an old DB."""
    genes = {
        name: MutationGene.objects.get(code=name)
        for name in ('tp53', 'pik3ca', 'esr1')
    }
    obsolete = {
        'c.472G>T (R158L)': genes['tp53'],
        'c.3146G>C (p.G1049A)': genes['pik3ca'],
        'c.1607T>A (L536Q)': genes['esr1'],
        'c.1609A>C (Y537S)': genes['esr1'],
    }
    for title, gene in obsolete.items():
        MutationCode.objects.update_or_create(
            code=code(title), defaults={'title': title, 'gene': gene},
        )

    reconcile_mutation_catalog()
    LoadGeneticMutations().load_all()

    assert not MutationCode.objects.filter(
        code__in=[code(title) for title in obsolete],
    ).exists()
    assert _seeded('tp53') == Counter(APPROVED['TP53'])
    assert _seeded('pik3ca') == (
        Counter(APPROVED['PIK3CA']) + Counter(PIK3CA_PENDING_ANSWER)
    )
    assert _seeded('esr1') == Counter(APPROVED['ESR1'])
