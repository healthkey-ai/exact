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
    'sort',
    'type',
    'benefitWeight',
    'patientBurdenWeight',
    'riskWeight',
    'distancePenaltyWeight',
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

        Ported verbatim from PROMOP's `TrialSearchPreferences`, including the
        exclusions, because the two stores answer the same question for the
        same badge while both exist. A count that disagreed between them would
        make the "Filters (N)" badge change when the host switched adapters.

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
    """
    from django.apps import apps
    from django.db import transaction

    from accounts.models import Identity

    models = list(apps.get_app_config('user_state').get_models())

    # Resolved before the transaction, so an unreachable model is a refusal
    # rather than a partial erasure. `Identity` may be reached through
    # another model in this app, so the path is a lookup chain, not one FK.
    paths = {}
    for model in models:
        direct = next(
            (f.name for f in model._meta.fields if f.related_model is Identity), None,
        )
        if direct:
            paths[model] = direct
            continue
        hop = next(
            (f for f in model._meta.fields
             if f.related_model is not None and f.related_model in paths), None,
        )
        if hop is None:
            raise RuntimeError(
                f'{model.__name__} is in user_state but nothing links it to '
                'Identity, directly or through another model in this app; '
                'forget_identity cannot erase it. Nothing has been deleted.'
            )
        paths[model] = f'{hop.name}__{paths[hop.related_model]}'

    with transaction.atomic():
        identity = Identity.objects.filter(issuer=issuer, sub=sub).first()
        if identity is None:
            logger.info('user_state.forget issuer=%s sub=%s nothing_here', issuer, sub)
            return {'found': False, 'removed': {}}

        removed = {}
        # Children first: deleting a parent would cascade and leave the child
        # counts unattributable.
        for model in reversed(models):
            _, per_label = model.objects.filter(**{paths[model]: identity}).delete()
            removed[model._meta.db_table] = per_label.get(model._meta.label, 0)

        Token.objects.filter(user=identity).delete()
        identity.delete()

    logger.info(
        'user_state.forget issuer=%s sub=%s removed=%s', issuer, sub, removed,
    )
    return {'found': True, 'removed': removed}
