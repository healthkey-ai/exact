"""Per-request pin of the active vocab-mirror release (#252 / ADR 0002).

When OMOP therapy matching is on, everything that reads the mirror in one request
must see ONE release generation. `MatchingReleaseContext` resolves
`active_release_id()` **once** at the request boundary and stashes it in a
request-scoped contextvar; mirror readers (e.g. title resolution) pick it up via
`active_pinned_release()`. A reader may also take an explicit `release_id` that
overrides the contextvar (the hybrid contract):
``rid = release_id if release_id is not None else active_pinned_release()``.

Scope of this slice: **presentation only** — the pinned release drives title /
vocabulary resolution and the ``X-Exact-OMOP-Release`` response header. Eligibility
still reads the materialized trial ``omop_*`` columns, not the mirror, so there is
deliberately **no fail-closed 503 here** — that lands with Phase-T, when the graph
traversal goes on the eligibility path.
"""
import contextvars

from vocab_mirror.activation import active_release_id
from trials.services.therapy_match_profile import omop_therapy_enabled

# Header naming the release a response's OMOP titles were resolved from (obs.).
RELEASE_HEADER = 'X-Exact-OMOP-Release'

# Request-scoped pin. The default sentinel means "not inside a pinned request"
# (non-HTTP callers) — distinct from a context that pins ``None`` (OMOP off / no
# active release), which must NOT fall back to the live active release.
_UNSET = object()
_pinned_release: contextvars.ContextVar = contextvars.ContextVar(
    'vocab_mirror_pinned_release', default=_UNSET)


def active_pinned_release():
    """The release mirror readers should use: the request pin if a
    ``MatchingReleaseContext`` set one (an int, or ``None`` when OMOP is off / no
    release is active), else — for callers outside any request (mgmt commands,
    model helpers) — the current active release. ``None`` ⇒ readers fail soft."""
    pinned = _pinned_release.get()
    if pinned is _UNSET:
        return active_release_id()
    return pinned


class MatchingReleaseContext:
    """Context manager that pins the active release for a request.

    Resolves the active release once (only when OMOP mode is on) and binds it to
    the request-scoped contextvar for the ``with`` block, so a single request
    never straddles an activation. Exposes ``header`` for ``X-Exact-OMOP-Release``.
    """

    def __init__(self):
        self.omop_active = omop_therapy_enabled()
        self.release_id = active_release_id() if self.omop_active else None
        self._token = None

    def __enter__(self):
        # release_id is already None whenever omop is off (see __init__).
        self._token = _pinned_release.set(self.release_id)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _pinned_release.reset(self._token)
        return False

    @property
    def header(self):
        if self.omop_active and self.release_id is not None:
            return str(self.release_id)
        return None
