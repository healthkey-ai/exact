"""Back-compat shim — moved to exact_matching.omop.therapy_graph (#5523)."""
from exact_matching.omop.therapy_graph import *  # noqa: F401,F403
from exact_matching.omop.therapy_graph import resolve_regimens, derive_component_and_type_values  # noqa: F401
