"""The therapy graph is package-owned (#5523).

CB could not inherit `eligible_for_therapy_related_things_from_lines` while its
legacy derivation lived only in EXACT's `trials` app: the packaged queryset
imports it at call time, and CB has no such module. The shim keeps EXACT's
import path working; this pins that it is a re-export, not a second copy.
"""
from exact_matching.omop import therapy_graph as packaged
from trials.services.omop import therapy_graph as host


def test_the_host_module_reexports_the_package_functions():
    assert host.derive_component_and_type_values is packaged.derive_component_and_type_values
    assert host.resolve_regimens is packaged.resolve_regimens
