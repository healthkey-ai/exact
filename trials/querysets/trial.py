"""Back-compat shim — moved to exact_matching.querysets.trial (E1.3d)."""
from exact_matching.querysets.trial import *  # noqa: F401,F403
# Explicit re-exports — `import *` skips underscore-prefixed names, but the
# dispatch test imports these private symbols by name from the old path.
from exact_matching.querysets.trial import (  # noqa: F401
    _CUSTOM_SEARCH_DISPATCH,
    _csv,
    _csv_stripped,
    _filter_therapy_lines_once,
)
