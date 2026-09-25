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
from django.conf import settings
from django.db import models
from rest_framework.authtoken.models import Token


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
            # `sort` and `type` are the sort control and the tab, not filters;
            # counting them would tick the badge up when the reader switches
            # tab.
            if key not in ('sort', 'type')
            and value not in (None, '', False, 0)
        )


def forget_identity(issuer: str, sub: str) -> dict:
    """Delete everything EXACT holds for one identity.

    Called by PROMOP when it deletes a patient. Written in the same commit as
    the table it empties, because the alternative is a service holding "this
    identity bookmarked these oncology trials" with nobody left to ask.

    EVERY MODEL IN THIS APP, enumerated rather than listed. Phase 2 adds
    favourites and phase 3 registration interest; naming one model here would
    mean each of those could land unwired, and the only signal would be a
    receipt that was silently incomplete. A test asserts the enumeration
    actually empties every table.

    The identity row goes too, and its auth tokens with it. Leaving them is
    not tidiness: `Identity` itself records that this person exists here, and
    a live token lets an in-flight request write the row back moments after
    it was erased.

    Returns what it removed, per table. `found` says whether there was an
    identity at all, so a sweep over identities PROMOP no longer knows can
    tell "never existed" from "already gone" instead of logging zero for both.
    """
    from django.apps import apps

    from accounts.models import Identity

    identity = Identity.objects.filter(issuer=issuer, sub=sub).first()
    if identity is None:
        return {'found': False, 'removed': {}}

    removed = {}
    for model in apps.get_app_config('user_state').get_models():
        # By the FK's own name rather than a hardcoded one, so a model added
        # with a differently-named link is a failing test and not a silently
        # skipped table.
        field = next(
            (f.name for f in model._meta.fields if f.related_model is Identity), None,
        )
        if field is None:
            raise RuntimeError(
                f'{model.__name__} is in user_state but has no link to Identity; '
                'forget_identity cannot erase it.'
            )
        count, _ = model.objects.filter(**{field: identity}).delete()
        removed[model._meta.db_table] = count

    Token.objects.filter(user=identity).delete()
    identity.delete()
    return {'found': True, 'removed': removed}
