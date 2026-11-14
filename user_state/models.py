"""State a USER of the trials page owns, keyed on the identity in their token.

Not the clinical record — see rule 5 in `docs/porting-from-cancerbot.md`,
which this app is the first user of. What lives here is meaningless outside
the trials page: the filters someone last searched with, the weights they gave
the score, whether they have been offered the wizard that sets those weights.

TWO THINGS ABOUT THE KEY, both of which are the point rather than detail.

It is the identity, not the patient. `AUTH_USER_MODEL` is `accounts.Identity`
(issuer + sub), built by `PartnerAuthentication` from a token signature EXACT
verifies itself, so it is not something a caller can assert. The `person_id`
a host sends arrives in the request body, bound to nothing — which is why
`?person_id=` is gated off as an IDOR (`exact/settings.py`) — and keying rows
on it would turn a read hazard into a write one: change the number, write into
someone else's saved filters. PROMOP can still resolve one to the other
whenever a backfill or an export needs it, because `Person.actor_iss` and
`Person.actor_sub` carry a UNIQUE constraint on the pair.

And it means these rows outlive the patient they describe, because EXACT is
not told when a patient is deleted. `forget_identity` below is how it is told;
it exists in the same commit as the table rather than after it.
"""
import logging

from django.conf import settings
from django.db import models
from django.db.models import Q
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)


# Keys that ride in the preferences payload but are not filters.
#
# `sort` is the sort control and `type` the tab: counting them would tick the
# "Filters (N)" badge up when the reader switches tab. The four weights change
# the ORDER of the list and the percentage on each card, never which trials
# are in it — the page says so in `frontend/src/federation/weights.ts`: "the
# Filters badge does not count them and Reset does not clear them". They ride
# in the same payload because the query key, the saved row and the export then
# need no second seam.
#
# So this list is what "not a filter" means, and both things that ask the
# question use it — the count and the reset. They were written separately and
# disagreed with the page on both: the count made four weights read as four
# filters, and the reset wiped them.
NOT_FILTERS = frozenset({
    # Qualifies `distance` and cannot be set without it, so counting it makes
    # one filter read as two — `filters.ts` says exactly that and the client
    # already excludes it. It IS persisted (`userOwnedFilters`), unlike the
    # two below, so it is the one key on this list the column really sees.
    'distanceUnits',
    # The four weights change the ORDER of the list and the percentage on
    # each card, never which trials are in it. `weights.ts`: "the Filters
    # badge does not count them and Reset does not clear them."
    'benefitWeight',
    'patientBurdenWeight',
    'riskWeight',
    'distancePenaltyWeight',
    # The sort control and the tab. This page never persists them — every
    # `savedFilters.persist` call site sends panel fields, `distanceUnits`
    # and weights — but PROMOP's copy of this column, written by an older
    # client, can hold them, and switching a host adapter must not change
    # the badge.
    'sort',
    'type',
})


class TrialSearchPreferences(models.Model):
    """The filters and weights one identity last used on the trial-search page.

    The payload is opaque on purpose, the same way it is opaque in PROMOP's
    copy: the filter vocabulary belongs to the search page
    (`study_preferences_from_query_params`), and mirroring it into columns
    would mean a migration every time that page gains a filter.
    """

    identity = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='trial_search_preferences',
        help_text="The verified identity these preferences belong to.",
    )
    preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Opaque filter payload, camelCase as the search page's query "
            "params spell it (searchTitle, trialType, phase, …). Not "
            "validated against a schema — see the class docstring."
        ),
    )
    weights_wizard_offered = models.BooleanField(
        default=False,
        help_text=(
            "Whether this identity has been offered the weights wizard. "
            "Offered once: a reader who dismissed it has answered."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_state_trial_search_preferences'
        verbose_name_plural = 'trial search preferences'

    def __str__(self):
        return f"Trial search preferences for identity {self.identity_id}"

    @property
    def non_default_filter_count(self) -> int:
        """How many filters this identity actually set.

        Started as PROMOP's `TrialSearchPreferences` copy, verbatim, because
        the two stores answer the same question for the same badge while both
        exist and a count that disagreed would make the badge change when the
        host switched adapters. It is no longer verbatim: `distanceUnits` and
        the four weights are excluded here and not there, because measured
        against THIS page's client they had to be — see `NOT_FILTERS`. That
        is a divergence from PROMOP, deliberately, and it is the direction
        that agrees with the page; PROMOP's copy is the one to change.

        Empty string, null and false are "unset" — a cleared text input hands
        back `""` before anything normalizes it, and an unticked checkbox is
        `false`. A zero distance counts as unset too: the search gates on
        `if study_info.distance:`, so zero applies no limit at all.
        """
        stored = self.preferences
        if not isinstance(stored, dict):
            # A row written from a shell, or before the serializer rejected a
            # non-object payload, must not make every read of it raise.
            return 0
        return sum(
            1
            for key, value in stored.items()
            if key not in NOT_FILTERS
            # An empty list is an unset multi-select, not a filter that is
            # set: clearing one must not leave the badge reading "Filters (1)".
            # `[] not in (None, '', False, 0)` is True, so it needs saying.
            and value not in (None, '', False, 0)
            and value != []
            and value != {}
        )


def erasure_paths(models, identity_model):
    """For each model, every ORM lookup path from it to the identity.

    Pure graph work, separated from the deleting so it can be tested on
    shapes this app does not have yet — which is the point, since every
    defect here so far has been about a shape nobody had built.

    To a FIXED POINT, not in one pass. Reachability is a property of the
    graph, and a single ordered walk decides it by SOURCE ORDER: a child
    declared above its parent came out "unreachable" and stopped erasure for
    every identity in the service until somebody reordered `models.py`. Three
    findings in one review were spellings of the same premise — that one pass
    over concrete forward FKs is enough — so this runs until it stops
    learning and only then refuses what is left.

    `get_fields()` rather than `fields`, so a many-to-many counts as a link:
    `_meta.fields` omits them, and a model linked only that way was told
    "nothing links it to Identity", which was untrue and fatal.

    Every path, not the first: a model reaching the identity two ways is
    erased by both, rather than by one and a cascade that the receipt cannot
    account for. Which means a model is resolved only once EVERY one of its
    links is — resolving it as soon as one parent is known loses the routes
    through parents that are still pending, and loses them silently, since
    those rows then die by cascade and never appear in the counts.
    """
    paths = {}
    remaining = list(models)
    while remaining:
        progress = False
        for model in list(remaining):
            links = [
                f for f in model._meta.get_fields()
                if getattr(f, 'related_model', None) is not None
                and getattr(f, 'concrete', False)
            ]
            direct = [f.name for f in links if f.related_model is identity_model]
            if direct:
                paths[model] = direct
                remaining.remove(model)
                progress = True
                continue
            # Only when every link is settled. A link to something still
            # unresolved may yet become a second route; taking the first one
            # known would drop it.
            unsettled = [f for f in links if f.related_model not in paths]
            hops = [f for f in links if f.related_model in paths]
            if hops and not unsettled:
                paths[model] = [
                    f'{hop.name}__{onward}'
                    for hop in hops
                    for onward in paths[hop.related_model]
                ]
                remaining.remove(model)
                progress = True
        if not progress:
            names = ', '.join(sorted(m.__name__ for m in remaining))
            raise RuntimeError(
                f'{names} in user_state, and nothing links them to Identity '
                'directly or through another model in this app; '
                'forget_identity cannot erase them. Nothing has been deleted.'
            )
    return paths


def forget_identity(issuer: str, sub: str) -> dict:
    """Delete everything EXACT holds for one identity.

    WHAT A RECEIPT FROM THIS FUNCTION GUARANTEES. Written down because the
    first two versions of it each broke a promise the version before had
    made, and a third guess would have broken a fourth:

    1. ALL OR NOTHING. Either every table this app owns is empty for this
       identity and the identity is gone, or nothing changed. Half an erasure
       is worse than none: it destroys data and leaves the subject on file.
    2. EVERY TABLE, found by enumeration rather than by a list somebody has
       to maintain — including tables that reach `Identity` through another
       model, which is the likely shape of phase 2 (a note on a favourite, a
       status history on an interest record). A model this cannot reach is a
       hard error BEFORE anything is deleted, never mid-way.
    3. THE COUNTS ARE PER TABLE, taken from `delete()`'s own per-label
       breakdown, not from its cascade total.
    4. IT IS IDEMPOTENT, and the receipt describes STATE, NOT HISTORY.
       "Nothing here" is the answer whether this identity was erased a minute
       ago or never existed, and that is deliberate: telling those apart
       would mean keeping a record that this person was once here, which is
       the thing being deleted. An earlier docstring promised the
       distinction, and the same commit made it impossible by deleting the
       identity row. Callers that need history keep their own log.
    5. IT WRITES ITS OWN AUDIT LINE, here rather than at the HTTP boundary,
       so the by-hand path records an erasure too. That path exists for the
       day the endpoint is broken, and that is not a day to be silent on.

    Points 4 and 5 look like they contradict each other and do not, but the
    line between them is worth stating because the first draft of point 4
    drew it wrongly. It said the distinction was refused because keeping it
    "would mean keeping a record that this person was once here" — and then
    point 5 writes issuer and sub to the application log, which is such a
    record and outlives the row. The real reason is narrower and true: the
    RECEIPT reports the state of the table, and the table is empty either
    way. Whether an erasure is logged, and for how long, is an operational
    decision with its own retention, taken deliberately here because an
    erasure nobody can show happened is one nobody can show happened.
    """
    from django.apps import apps
    from django.db import transaction

    from accounts.models import Identity

    models = list(apps.get_app_config('user_state').get_models())

    # Resolved before the transaction, so an unreachable model is a refusal
    # rather than a partial erasure. See `erasure_paths`.
    paths = erasure_paths(models, Identity)

    with transaction.atomic():
        identity = Identity.objects.filter(issuer=issuer, sub=sub).first()
        if identity is None:
            logger.info('user_state.forget issuer=%s sub=%s nothing_here', issuer, sub)
            return {'found': False, 'removed': {}}

        removed = {}
        # Children first: deleting a parent would cascade and leave the child
        # counts unattributable. Depth in the resolved graph, not source
        # order — the order models happen to be declared in is exactly what
        # the resolver above stopped depending on.
        by_depth = sorted(models, key=lambda m: max(p.count('__') for p in paths[m]))
        for model in reversed(by_depth):
            query = Q()
            for path in paths[model]:
                query |= Q(**{path: identity})
            _, per_label = model.objects.filter(query).delete()
            removed[model._meta.db_table] = per_label.get(model._meta.label, 0)

        Token.objects.filter(user=identity).delete()
        identity.delete()

    logger.info(
        'user_state.forget issuer=%s sub=%s removed=%s', issuer, sub, removed,
    )
    return {'found': True, 'removed': removed}
