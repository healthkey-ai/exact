"""Reconcile EXACT's reference catalogue with a corrected mutation catalog (#5297).

CancerBot retires catalog rows with data migrations 0456-0460. EXACT owns no
migrations for the trials DB, so its seeder removes the same historical codes
before loading; upserting alone would keep obsolete options selectable on a
database seeded before the correction. This is an intentional EXACT-only
divergence: the shared ``LoadGeneticMutations`` stays a verbatim port.
"""
from trials.models import MutationCode
from trials.services.genetic_mutation_catalog import code, old_titles


def reconcile_mutation_catalog():
    """Delete the catalog codes CancerBot's migrations retired."""
    MutationCode.objects.filter(
        code__in={code(title) for title in old_titles()},
    ).delete()
