import csv
import io as _io
import logging
import re
from typing import TYPE_CHECKING, Optional

from django.db.models import Case, F, IntegerField, Prefetch, QuerySet, Value, When
from django.utils.functional import SimpleLazyObject, cached_property
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import viewsets, filters, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from trials.api.pagination import TrialsPagination
from trials.api.utils import csv_safe_cell
from trials.api.trials_serializers import TrialSerializer, TrialDetailsSerializer
from trials.models import Trial, Location, LocationTrial, PreferredCountry, State
from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount
from trials.services.patient_info.resolve import resolve_patient_info
from trials.services.study_preferences import StudyPreferences, study_preferences_from_query_params
from trials.services.utils import parse_goodness_weights
from trials.services.value_options import ValueOptions

logger = logging.getLogger(__name__)


#: Excel refuses a cell past 32,767 characters, and a trial with several
#: hundred sites — ordinary — can produce one. Cut well short of the limit so
#: the suffix explaining the cut fits inside it too.
EXPORT_CELL_LIMIT = 30_000


def _clip(text: str) -> str:
    """One cell, short enough for a spreadsheet to accept.

    Applies to scalars as much as to joined lists: `brief_title` and
    `official_title` are unbounded `TextField`s, and one long enough takes the
    whole file down with it. Saying the cell was cut matters for the same
    reason the export's own sentinel does — silently shorter reads as
    genuinely shorter.
    """
    if len(text) <= EXPORT_CELL_LIMIT:
        return text
    dropped = len(text) - EXPORT_CELL_LIMIT
    return text[:EXPORT_CELL_LIMIT] + f' … and {dropped} more characters'


def _export_cell(value):
    """One serialized field, as a CSV cell.

    Lists are joined rather than written as their Python repr: `phase` and
    `location` arrive as arrays, and `['PHASE2']` in a spreadsheet column is
    not something a reader can do anything with. `None` is an empty cell, not
    the string "None" — a spreadsheet reads the latter as data.

    Free text goes through `csv_safe_cell`; numbers are handed back as numbers
    so the spreadsheet can still sum them, and so a negative one is not quoted
    into text by its leading `-`.
    """
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        # Each element neutralised before the join, not the joined string: a
        # payload in the SECOND location would otherwise sail through.
        parts = [str(csv_safe_cell(v)) for v in value if v is not None]
        joined = '; '.join(parts)
        if len(joined) <= EXPORT_CELL_LIMIT:
            return joined
        # Cut, and SAYING it was cut. A cell that Excel refuses takes the whole
        # file down with it; one that quietly ends mid-list reads as a trial
        # with fewer sites than it has.
        kept: list[str] = []
        length = 0
        for part in parts:
            if length + len(part) + 2 > EXPORT_CELL_LIMIT:
                break
            kept.append(part)
            length += len(part) + 2
        return '; '.join(kept) + f'; … and {len(parts) - len(kept)} more'

    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)):
        return value
    # Clipped BEFORE neutralising, so the apostrophe a formula-looking value
    # gains is not what pushes the cell over the limit.
    return csv_safe_cell(_clip(value if isinstance(value, str) else str(value)))


def _export_mcl_cell(trial, patient_info) -> str:
    """The high-risk MCL rule, flattened into one cell.

    Only for a trial that names criteria — the matcher is built per row here,
    and constructing one for every trial in an export to be told "no criteria"
    is a cost paid on the disease's whole corpus. The three lists are plain
    columns, so the question is answered before the matcher exists.
    """
    if patient_info is None:
        return ''
    if not any((
        trial.high_risk_mcl_criteria_required,
        trial.high_risk_mcl_criteria_excluded,
        trial.high_risk_mcl_criteria_sufficient_any,
    )):
        return ''
    from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
    breakdown = UserToTrialAttrMatcher(trial=trial, patient_info=patient_info).high_risk_mcl_criteria_breakdown()
    if not breakdown:
        return ''
    required = breakdown.get('required') or []
    sufficient = breakdown.get('sufficientAny') or []
    excluded = breakdown.get('excluded') or []

    def inclusion(code_status):
        # The server reports in ELIGIBILITY terms and this file has no legend,
        # so the words are the reader's: `matched` here means they have it.
        return {
            'matched': 'you have this',
            'not_matched': 'you do not have this',
            'unknown': 'not known from your data',
        }.get(code_status['status'], code_status['status'])

    def exclusion(code_status):
        # The inverse, which is the whole reason this is not one shared map:
        # on an excluded criterion `matched` means CONFIRMED ABSENT, so
        # printing the raw word tells the reader the opposite of the truth.
        return {
            'matched': 'you are clear of this',
            'not_matched': 'you have this — it rules this trial out',
            'unknown': 'not known from your data',
        }.get(code_status['status'], code_status['status'])

    parts = [f"verdict={breakdown['aggregate']}"]
    if required:
        # "Either" when the trial also names alternatives, because the server
        # ORs the two inclusion rules — calling it a requirement would tell a
        # patient who qualifies through the alternatives that they failed
        # something.
        head = 'either' if sufficient else 'requires'
        parts.append(
            f"{head} at least {breakdown.get('minCount', 1)} of these "
            f"(you have {breakdown.get('matchedCount', 0)}): "
            + ', '.join(f"{c['code']} — {inclusion(c)}" for c in required)
        )
    if sufficient:
        head = 'or any one of these' if required else 'any one of these qualifies'
        parts.append(f"{head}: " + ', '.join(f"{c['code']} — {inclusion(c)}" for c in sufficient))
    if excluded:
        parts.append(
            'rules the trial out: '
            + ', '.join(f"{c['code']} — {exclusion(c)}" for c in excluded)
        )
    return ' | '.join(parts)

#: A trial id as it may appear in a JSON string: ASCII digits, nothing else,
#: and at most as many as a bigint can hold. `str.isdigit()` is not
#: equivalent — see `TrialsViewSet._trial_id`.
#:
#: The length bound is not cosmetic: Python refuses to parse an integer past
#: a digit limit (4300 by default), so an unbounded match sends a 5000-digit
#: string into `int()` and the `ValueError` escapes the view as a 500 — the
#: same failure the ASCII match was added to close.
_TRIAL_ID_RE = re.compile(r'\d{1,19}', re.ASCII)

#: The largest value the `id` column can hold (`BigAutoField` -> signed
#: bigint). Anything above it is not merely a miss: PostgreSQL types the
#: constant as `numeric`, which coerces the column in the `IN` comparison and
#: gives up the primary-key index — so 500 such ids turn a bookmarks lookup
#: into a table scan.
_MAX_TRIAL_ID = 2 ** 63 - 1

if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo


# ---------------------------------------------------------------------------
# Inline serializers for lookup tables
# ---------------------------------------------------------------------------

class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PreferredCountry
        fields = ['id', 'code', 'title']


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['id', 'title', 'city', 'state_id', 'country_id']

    def to_representation(self, instance):
        response = super().to_representation(instance)
        response['state'] = instance.state.title if instance.state else None
        response['country'] = instance.country.title if instance.country else None
        return response


# ---------------------------------------------------------------------------
# Trials ViewSet
# ---------------------------------------------------------------------------

class TrialsViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    default_serializer_class = TrialDetailsSerializer
    serializer_classes = {
        'list': TrialSerializer,
        'search': TrialSerializer,
        'retrieve': TrialDetailsSerializer,
    }
    filter_backends = [filters.SearchFilter]
    search_fields = ['brief_title', 'official_title']
    pagination_class = TrialsPagination

    def _resolve_patient_info(self) -> Optional['PatientInfo']:
        # Resolve at most once per request: get_queryset and
        # get_serializer_context both call this, and `search` can hit it
        # several times. Re-resolving re-runs the CTOMOP round-trip, so a
        # slow upstream multiplies per request (#159, #160). The holder is a
        # 1-tuple so a legitimately-resolved None is still cached.
        holder = getattr(self.request, '_exact_patient_info', None)
        if holder is not None:
            return holder[0]

        from trials.services.patient_info.resolve import PATIENT_INFO_KEYS

        data = getattr(self.request, 'data', None)
        # Both spellings: the camelCase one is what docs/api.md documents,
        # and treating it as "no inline payload" is what made the documented
        # request answer with the unfiltered catalog (#375).
        has_inline = isinstance(data, dict) and any(
            bool(data.get(key)) for key in PATIENT_INFO_KEYS
        )

        try:
            patient_info = resolve_patient_info(self.request)
        except APIException:
            # Already an HTTP-meaningful response (e.g. PermissionDenied from
            # the person_id IDOR gate, #150) — let DRF render it as-is.
            raise
        except Exception:
            # Don't swallow into a silent None: that would run the matcher with
            # no patient context and return an unfiltered/unscored trial list
            # that looks valid — dangerous in a clinical matcher (#156).
            logger.exception('Failed to build patient_info from request payload')
            # Only a supplied inline payload that fails to build is client error
            # (400). The CTOMOP fetch returns None on network failure (handled in
            # CtomopClient), so an exception on the person_id path is a real
            # server/upstream bug — let it propagate as a 500 rather than masking
            # it as a misleading 400.
            if has_inline:
                raise serializers.ValidationError(
                    {'patient_info': 'Could not build patient context from the supplied payload.'}
                )
            raise

        self.request._exact_patient_info = (patient_info,)
        return patient_info

    def _resolve_study_preferences(self) -> StudyPreferences:
        return study_preferences_from_query_params(self.request.query_params)

    def _reject_user_scoped_search_type(self, search_type):
        """`?type=favorites` / `?type=my_trials` have no meaning here yet.

        Both name a per-user relation to a trial, and EXACT holds no per-user
        state: the search types were inherited from CB, where `add_favorite()`
        / `add_for_participation()` annotate the queryset from `UserTrial`.
        Neither annotation exists here, so what the two types did instead was:

        - `favorites`: `queryset.filter(favorite=True)` against a field the
          Trial model does not have — a FieldError surfacing as a 500.
        - `my_trials`: no narrowing at all. `filtered_trials` routes both
          through `filter_for_admin`, which skips the eligibility filter, so
          the response was the whole corpus presented as the patient's
          registered trials — wrong in a way nothing on screen reveals.

        This rejection is permanent, not a placeholder. The real path has
        since landed and is a different shape: the favorites list lives in
        PROMOP and comes down as a bounded `trial_ids` filter, which narrows
        inside the queryset (`_resolve_trial_ids` below; the federated UI's
        Favorites tab uses it and never sends this type). Accepting
        `?type=favorites` would mean accepting a search type that narrows
        nothing — the plausible wrong answer this exists to prevent. So the
        400 stays, and names the path that works.

        `not_eligible` goes too, for the same reason wearing different
        clothes: it matches no branch in `add_potential_attrs_count`, so it
        fell through to no narrowing at all and returned the eligible +
        potential set — the exact inverse of its name. It was in the API
        docs, so a client could be asking for it today and rendering
        "trials you do not qualify for" over trials the patient does.

        `all` is deliberately left alone. It reaches the same admin branch
        and returns the same unnarrowed corpus, but that corpus is what it
        names — a caller asking for every trial gets every trial. The
        others promise a subset they cannot deliver.
        """
        if search_type in ('favorites', 'my_trials'):
            raise serializers.ValidationError({
                'type': [
                    f"'{search_type}' is not supported: EXACT stores no per-user "
                    f"trial state. POST the ids to filter by as `trial_ids` "
                    f"instead — see /trials/search/match/."
                ]
            })
        if search_type == 'not_eligible':
            raise serializers.ValidationError({
                'type': [
                    "'not_eligible' is not supported: the queryset has no such "
                    "narrowing, and the value used to return the eligible and "
                    "potential trials instead — the inverse of what it names."
                ]
            })

    #: The most bookmarks one request may filter by. Every id becomes part
    #: of an `IN (...)`, so an unbounded list is a request that expands into
    #: thousands of clauses — and a caller with that many bookmarks needs a
    #: different feature, not a longer query.
    MAX_TRIAL_IDS = 500

    def _resolve_trial_ids(self):
        """The `trial_ids` filter from the request body, or None.

        This is how the federated UI's Favorites tab works: the bookmarks
        live in PROMOP, which knows nothing about matching, so the ids come
        down here and the narrowing happens inside the queryset — the only
        place that can sort and paginate them alongside the match scores,
        and produce a total that agrees with what it listed.

        Body, not query string: the list can be long and the patient payload
        is already travelling in the body on this path.

        `None` means "no such filter". An EMPTY LIST does not: it means the
        caller asked for their bookmarks and has none, so the honest answer
        is no trials. Treating `[]` as absent would answer an empty
        Favorites tab with the entire corpus.
        """
        # In the query string it is a mistake, and a quiet one: ignored, the
        # request answers a bookmarks question with the whole corpus — the
        # same wrong answer `[]` is guarded against, through another door.
        if 'trial_ids' in self.request.query_params:
            raise serializers.ValidationError({
                'trial_ids': [
                    'send trial_ids in the request body, not the query string '
                    '— see POST /trials/search/match/.'
                ]
            })

        data = getattr(self.request, 'data', None)
        if not isinstance(data, dict) or 'trial_ids' not in data:
            return None

        raw = data.get('trial_ids')
        if not isinstance(raw, (list, tuple)):
            raise serializers.ValidationError(
                {'trial_ids': ['must be a list of trial ids.']}
            )
        if len(raw) > self.MAX_TRIAL_IDS:
            raise serializers.ValidationError({
                'trial_ids': [
                    f'at most {self.MAX_TRIAL_IDS} ids may be sent; got {len(raw)}.'
                ]
            })

        ids = []
        for value in raw:
            ids.append(self._trial_id(value))
        return ids

    @staticmethod
    def _trial_id(value):
        """One id, or a 400.

        Strict on purpose — every loose parse here turns into a filter the
        caller did not ask for:

        - `True` is an `int` in Python, so `int(True)` is trial 1.
        - `int(3.5)` is 3, so a float silently becomes a DIFFERENT trial
          rather than being refused.
        - `str.isdigit()` is true for characters `int()` then refuses —
          `'²'`, `'³'` — so guarding with it and converting afterwards
          raises `ValueError` out of the view as a 500, not a 400.
        - It is also true for fullwidth digits, where `int('１２３')` does
          succeed and quietly yields a different trial than the literal
          text the caller sent.

        Hence an explicit ASCII-digits match rather than a character
        predicate. Numeric strings are accepted because JSON from a browser
        routinely carries ids that way; a negative one is refused because a
        primary key is never negative, and admitting a sign is what let
        `'--5'` through the old guard.
        """
        if isinstance(value, bool) or isinstance(value, float):
            raise serializers.ValidationError(
                {'trial_ids': [f'{value!r} is not a trial id.']}
            )
        if isinstance(value, int):
            return TrialsViewSet._in_range(value, value)
        if isinstance(value, str) and _TRIAL_ID_RE.fullmatch(value.strip()):
            return TrialsViewSet._in_range(int(value), value)
        raise serializers.ValidationError(
            {'trial_ids': [f'{value!r} is not a trial id.']}
        )

    @staticmethod
    def _in_range(number, original):
        """Refuse an id the `id` column could not hold.

        Not pedantry about a value that simply would not match: above the
        bigint range PostgreSQL types the constant as `numeric`, coerces the
        column to compare, and stops using the primary-key index. A list of
        500 of those is a table scan wearing the shape of a bookmarks
        lookup.

        Negative is refused for its own reason — a primary key is never
        negative, and admitting a sign is what let `'--5'` past an earlier
        version of this guard.
        """
        if 0 <= number <= _MAX_TRIAL_ID:
            return number
        raise serializers.ValidationError(
            {'trial_ids': [f'{original!r} is not a trial id.']}
        )

    def get_queryset(self, patient_info=None):
        # Before `_resolve_patient_info`, which can be a round trip to
        # PROMOP: a malformed body should not pay for that to be told it is
        # malformed.
        trial_ids = self._resolve_trial_ids()

        if patient_info is None:
            patient_info = self._resolve_patient_info()

        queryset = Trial.objects.all()
        study_prefs = self._resolve_study_preferences()
        search_type = self.request.query_params.get('type', None)
        # Only where `type` is actually consumed. It reaches `filtered_trials`
        # from `list` and `count` as well as `search`, so all three are
        # covered — but `retrieve` ignores the parameter entirely, and
        # rejecting it there would turn a working detail page into a 400 for
        # any UI that carries the tab it came from in the query string.
        if self.action in ('list', 'count', 'search'):
            self._reject_user_scoped_search_type(search_type)

        if trial_ids is not None:
            # Before the matcher, not after: narrowing first means the
            # scores, the ordering and `itemsTotalCount` are all computed
            # over the set the caller asked about.
            queryset = queryset.filter(id__in=trial_ids)

        if self.action in ['list', 'count', 'search']:
            queryset, _ = queryset.filtered_trials(
                search_options=self.request.query_params,
                study_info=study_prefs,
                patient_info=patient_info,
                add_traces=False,
                search_type=search_type,
            )

        # A saved trial the patient no longer qualifies for is still their
        # saved trial. Dropping it made the Favorites tab read "1" over the
        # words "No trials found" — two numbers on one screen contradicting
        # each other, with the trial unreachable from the tab that counts it
        # (#568). It comes back here, and `matchingType` says `not_eligible`
        # so the card can mark it.
        #
        # `matched_for_counts` holds the set as it was BEFORE widening: the
        # tab counts are a claim about who qualifies, so they keep answering
        # over the rows the eligibility filter kept. `counts` is computed
        # from it too, which is what makes every other row's
        # `attributesToFillIn` byte-identical to before this change.
        matched_for_counts = None
        self._unmatched_saved_ids = frozenset()
        if self.action in ('list', 'search') and self._keeps_unmatched_saved(
            trial_ids, search_type
        ):
            matched_for_counts = queryset
            queryset = self._widened_saved(
                queryset, trial_ids, study_prefs, patient_info,
            )

        # The leading sort key, present only when `_widened_saved` annotated
        # one. An empty tuple everywhere else, so no other request's ordering
        # gains a column.
        sunk_first = ('saved_not_matching',) if self._unmatched_saved_ids else ()

        if self.action in ['list', 'search', 'retrieve']:
            # Parsed one weight at a time: an unreadable value falls back on
            # its own rather than taking the other three with it. See
            # `parse_goodness_weights`.
            queryset = queryset.with_goodness_score_optimized(
                **parse_goodness_weights(self.request.query_params),
                geo_point=patient_info.geo_point if patient_info else None,
                recruitment_status=study_prefs.recruitment_status,
            )

        if self.action == 'retrieve':
            queryset = queryset.with_distance_optimized(
                patient_info.geo_point if patient_info else None,
                recruitment_status=study_prefs.recruitment_status,
            )

        if self.action in ('list', 'retrieve'):
            # Annotates `match_score` (and potential_attrs_count). The detail
            # page shows the Matching Score too, so `retrieve` needs this —
            # without it `TrialDetailsSerializer` reads `match_score` as None
            # and the detail page shows "N/A" for every trial.
            queryset = queryset.with_potential_attrs_count(patient_info)

        if self.action == 'list':
            queryset = queryset.order_by(
                *sunk_first, '-match_score', '-posted_date', 'id'
            )

        if self.action == 'search':
            counts_source = (
                queryset if matched_for_counts is None else matched_for_counts
            )
            counts = self._trials_counts(counts_source, patient_info)
            # Annotated but NOT narrowed by `type`, kept for the tab counts.
            # `with_potential_attrs_count` both annotates and applies the
            # eligible/potential filter, so counting the response's own
            # queryset would count an already-narrowed set: on the Potential
            # tab the Eligible badge would read 0. The tab bar has to be
            # told about the whole matched corpus, not the tab it is on.
            self._tab_counts_source = counts_source.with_potential_attrs_count(
                patient_info, None, counts,
            )
            self._tab_counts_patient_info = patient_info
            self._tab_counts_search_type = search_type
            queryset = queryset.with_potential_attrs_count(patient_info, search_type, counts)

            sort_by = self.request.query_params.get('sort', 'goodnessScore')
            avail_sorts = ('distance', 'status', 'phase', 'updated', 'enrollment',
                           'patientBurdenScore', 'goodnessScore', 'matchScore')
            if sort_by not in avail_sorts:
                sort_by = 'goodnessScore'

            order = []
            if sort_by == 'distance' and patient_info and patient_info.geo_point:
                if 'distance' not in queryset.query.annotations:
                    queryset = queryset.with_distance_optimized(
                        geo_point=patient_info.geo_point,
                        recruitment_status=study_prefs.recruitment_status,
                    )
                order.append(F('distance').asc(nulls_last=True))
            elif sort_by == 'status':
                queryset = queryset.with_status_code()
                order.append(F('status_code').asc(nulls_last=True))
            elif sort_by == 'phase':
                order.append(F('phase_code_min').asc(nulls_last=True))
            elif sort_by == 'updated':
                order.append(F('last_update_date').desc(nulls_last=True))
            elif sort_by == 'enrollment':
                order.append(F('enrollment_count').desc(nulls_last=True))
            elif sort_by == 'patientBurdenScore':
                order.append(F('patient_burden_score').asc(nulls_last=True))
            elif sort_by == 'matchScore':
                order.append(F('match_score').desc(nulls_last=True))
            else:  # goodnessScore
                order.append(F('goodness_score').desc(nulls_last=True))
            order.append(F('id').asc())
            queryset = queryset.order_by(*sunk_first, *order)

        if self.action in ['list', 'search']:
            # `export` binds itself to 'search', so it is prefetched here too —
            # without it the stream issues a query per row for the locations
            # the serializer sorts by distance.
            queryset = queryset.prefetch_related(
                Prefetch('locationtrial_set',
                         queryset=LocationTrial.objects.select_related('location'))
            )

        return queryset

    #: The requested ids the eligibility filter dropped, which the serializer
    #: turns into `matchingType: not_eligible`. A frozenset so the default is
    #: a real answer ("none of them") rather than a missing attribute the
    #: serializer has to guess about.
    _unmatched_saved_ids: frozenset = frozenset()

    #: Whether this view's rows can carry the `not_eligible` mark. False on
    #: the graph, which buckets its nodes on `match_score` alone
    #: (`GraphTrialNodeSerializer`) and would file a widened trial under
    #: "fully matched" — a verdict flipped, not merely unmarked. The graph
    #: binds `self.action = "search"`, so the action is not what tells them
    #: apart.
    widens_saved_trials = True

    def _keeps_unmatched_saved(self, trial_ids, search_type) -> bool:
        """Whether this request lists saved trials the patient fails.

        - **This view can mark them.** See `widens_saved_trials`.
        - **Ids were asked for.** Without them this is the corpus search, and
          widening it would answer "which trials match me" with trials that
          do not.
        - **No explicit `?type=`.** `eligible` and `potential` name a verdict;
          handing back rows that hold neither answers a different question.
          `all` never reaches here — it skips the eligibility filter upstream.

        The caller restricts this to `list` and `search`. Not `count`: it is
        GET-only, and `trial_ids` travels in the body — the condition could
        never be true there, and this file cites the reason three times over.
        Not `retrieve` either, which never narrows by eligibility, so a
        `trial_ids` list excluding the id in the URL still answers 404.

        Notably NOT a condition: a patient. With none nothing was judged, so
        `_widened_saved` finds no ids to mark and hands back the set it was
        given — the response is identical either way, and the only difference
        is one bounded id query on a path the federated list never takes. A
        guard for that would be a branch no test can tell from its absence:
        measured, a patient-less saved-ids search issues 6 queries against 10
        for the same search with a patient, so no comparison isolates the one
        it would save.
        """
        return (
            self.widens_saved_trials
            and trial_ids is not None
            and search_type is None
        )

    def _widened_saved(self, matched, trial_ids, study_prefs, patient_info):
        """`matched`, plus the requested ids the eligibility filter dropped.

        Both sets are read off querysets rather than recomputed, so
        "not eligible" means exactly one thing: the filter that hides a trial
        is the same one that marks it. A verdict derived some other way — the
        Python matcher, say — can disagree with the filter (#455, #466), and a
        row that appears ONLY because the filter dropped it, carrying no mark
        because a second opinion kept it, would read as a match. That is the
        bug this exists to remove, reintroduced from the other side.

        The difference is taken against the WIDENED set, not against the ids
        the caller sent. Those two differ by the ids the reader's own filters
        dropped — a saved trial outside the phase they picked — and marking
        one of those `not_eligible` would blame the matcher for the reader's
        own choice. They never reach a row either way, which is precisely why
        the imprecise version would have gone unnoticed.

        Note what widening also drops: `filter_by_patient_info` carries one
        narrowing that is not an eligibility judgement, `disease__iexact`. So
        a saved trial for another disease comes back too. That is the right
        answer on this path — these are ids the reader bookmarked by hand, and
        a bookmark the tab refuses to show is the thing being fixed — but it
        is a real widening and not a side effect worth discovering later.
        """
        matched_ids = set(matched.values_list('id', flat=True))
        if len(matched_ids) == len(set(trial_ids)):
            # Every id the caller sent came back, so nothing was dropped by
            # anything and there is no second queryset to build. Note the
            # cost this does NOT save: the id read above is unconditional, so
            # every saved-tab request pays one extra round trip (measured
            # 9 → 10 queries, and 11 when a row is actually widened).
            #
            # It is also a length test, not a set test, so a bookmark for a
            # trial the catalog no longer holds defeats it: the lengths
            # differ, the second queryset gets built, and it finds nothing to
            # mark. Correct, and a real case — the bookmarks live in PROMOP
            # while this catalog is reloaded — just not free.
            return matched

        widened, _ = Trial.objects.filter(id__in=trial_ids).filtered_trials(
            search_options=self.request.query_params,
            study_info=study_prefs,
            patient_info=patient_info,
            add_traces=False,
            search_type=None,
            narrow_by_patient=False,
        )
        self._unmatched_saved_ids = (
            frozenset(widened.values_list('id', flat=True)) - matched_ids
        )
        if not self._unmatched_saved_ids:
            return matched

        # Sunk to the bottom of whatever order the reader chose. Every sort
        # this list offers ranks by a column the widened rows still hold, and
        # the default one is the `match_score` ANNOTATION, which reads 100 on
        # a trial the patient conflicts with — so the failing bookmark sorted
        # FIRST, ahead of the ones that match. The serializer sends 0 for
        # those rows, so without this the list was also ordered by a number
        # it does not report. The mark says these rows are different; the
        # order has to agree with it.
        return widened.annotate(
            saved_not_matching=Case(
                When(id__in=self._unmatched_saved_ids, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    @cached_property
    def trials_counts(self):
        """How many trials leave each of the patient's blank attributes
        unanswered — what `attributesToFillIn` ranks by, and what decides
        whether a row reads `eligible` or `potential`.

        Reaching the serializer at all is #464: `get_serializer_context` set
        `'counts': {}` as a literal, inherited from the initial port.
        `potential_attrs_for_trial` loops over `counts.keys()`, so the loop
        never ran — `attributesToFillIn` was `[]` on every row of every list
        response and the verdict derived from it was the constant `eligible`.

        Shaped after CB's `trials_counts` (`trials/api/trials_views.py:231`),
        which this is a port of, down to the `type=all` widening. Three
        things follow, none incidental.

        `search_options={}` is copied from CB and is INERT in both repos —
        `filtered_trials` declares the parameter and never reads it. Passing
        the request's own query params would build a byte-identical scope. It
        is written this way because CB writes it this way, not because it is
        what drops the request's filters: the reader's filters DO reach this
        scope, through `study_info`.

        One more deviation, recorded rather than hidden: CB keeps the
        else-branch scope construction inside `blank_attribute_records_count`,
        reached by passing `user=`. EXACT's helper has no `user` (rule 5), so
        the branch is built here. A closer shape would be to give the helper a
        `study_info=` parameter so the construction stays in the file CB keeps
        it in, and the next port of that helper lands clean; that is a change
        to a shared helper and belongs in its own commit.

        - **Its own scope, not the request's queryset.** The count on a chip
          answers "fill this in and N more trials can be judged", so N is about
          the patient's matched corpus, not about the page. Taken from
          `get_queryset`'s queryset it moved with the tab — on Favorites,
          narrowed to the saved ids, "unlocks 1 trial" — and `list` and
          `search` reach that queryset at different points, so
          `/trials/match/` and `/trials/search/match/` answered 2 and 1 for one
          patient and one trial. Measured, before this took CB's shape.

          It costs one aggregate rather than none: the tab counts are taken
          over a different set, so there is nothing to reuse. Measured, +1
          query on any list or search response that names a patient.
        - **Once per request.** A `cached_property`, so the several contexts
          built during one response share one aggregate. NOT lazy in the sense
          that would be nice: `get_serializer_context` puts the value in a
          dict, which forces it even where the serializer will not read it —
          `?type=eligible` short-circuits to `attrs_to_fill_in = []` and still
          pays. CB is eager in the same place, and rule 1 of the porting doc
          says not to improve on it mid-port. The early return below does save
          the patient-less request from building a scope to answer `{}`.
        - **`user=` is not ported.** CB's helper takes the user and looks up
          their saved `StudyInfo` and `PatientInfo` rows. EXACT is stateless by
          rule 5, so the scope is built here from what the request carries.

        What is NOT taken from here is the `counts` that
        `with_potential_attrs_count` receives, which stays on the queryset the
        tab counts are computed over. CB feeds both from this one property;
        matching that moves `potential_profit_avg`, a different field with its
        own reasons, and belongs in its own change.

        So one response now carries two different corpora, and that is
        deliberate on both sides. `_tab_counts` applies `filter_queryset`, so
        the bar narrows with `?search=` and with a `trial_ids` list; this does
        not, because "fill this in and N more trials can be judged" is not a
        statement about the page. Measured: a `trial_ids`-narrowed search
        answers `tabCounts {potential: 2}` beside a chip reading 5. Faithful
        to CB, and asserted on purpose in
        `TestWhatTheCountIsAbout` — but a UI that renders the two side by side
        would be showing a reader two numbers that look comparable and are
        not. Tracked in #577.
        """
        patient_info = self._resolve_patient_info()
        if patient_info is None:
            return {}

        search_type = self.request.query_params.get('type', None)
        if search_type == 'all':
            scope = Trial.objects.all()
        else:
            scope, _ = Trial.objects.all().filtered_trials(
                search_options={},
                study_info=self._resolve_study_preferences(),
                patient_info=patient_info,
                add_traces=False,
            )
        return self._trials_counts(scope, patient_info)

    def _trials_counts(self, queryset: QuerySet, patient_info: Optional['PatientInfo']) -> dict[str, int]:
        return BlankAttributeRecordsCount().counts(queryset, patient_info)

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, self.default_serializer_class)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        patient_info = self._resolve_patient_info()
        study_prefs = self._resolve_study_preferences()
        template = self.request.query_params.get('view', None)
        search_type = self.request.query_params.get('type', None)

        context.update({
            'patient_info': patient_info,
            'distance_units': study_prefs.distance_units,
            'recruitment_status': study_prefs.recruitment_status,
            # Lazily, which CB does not need to be. Its context is read by
            # one serializer; ours is shared with `TrialDetailsSerializer`,
            # which never looks at `counts` — so putting the VALUE here ran a
            # corpus-wide aggregate on every detail request and threw it away
            # (measured: 209 queries to 210, on the page a reader opens per
            # trial). `SimpleLazyObject` keeps the line reading as CB's while
            # charging the query to whoever actually reads it.
            #
            # Two edges it brings, neither reached today. The value is no
            # longer a `dict`: the serializer forces it with `or {}` and the
            # mapper only ever iterates it, but `isinstance(x, dict)`,
            # `json.dumps` or a context copy would behave differently. And in
            # `export` the aggregate now runs inside the streaming generator,
            # after the 200 has gone out — so a failure there arrives as the
            # file's `EXPORT INCOMPLETE` marker rather than as a 500, which is
            # the better of the two but is not what a reader would assume.
            'counts': SimpleLazyObject(lambda: self.trials_counts),
            'template': template,
            'search_type': search_type,
            'explain': self.request.query_params.get('explain', '').lower() == 'true',
            # Empty unless `get_queryset` widened a saved-ids search (#568).
            # `get_queryset` runs first on every action that builds a list, so
            # by the time a serializer asks, the answer is settled.
            'unmatched_trial_ids': self._unmatched_saved_ids,
        })
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        # #4408: per-criterion high-risk MCL explainability (detail view only;
        # None for trials that gate on no high-risk criteria, or without a patient).
        patient_info = self._resolve_patient_info()
        if patient_info is not None:
            from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
            data['highRiskMclCriteriaBreakdown'] = (
                UserToTrialAttrMatcher(trial=instance, patient_info=patient_info)
                .high_risk_mcl_criteria_breakdown()
            )
        return Response(data)

    def get_paginated_response(self, data, extra_keys=None):
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data, extra_keys=extra_keys)

    @action(methods=['get'], detail=False)
    def count(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        return Response({'count': queryset.count()})

    @action(methods=['post'], detail=False, url_path='match')
    def match(self, request, *args, **kwargs):
        """POST alias for the list endpoint, identical response shape.

        Exists so the browser-side harness can carry `patient_info`
        in the request body. The Fetch spec forbids GET-with-body
        (`new Request('/trials/', { method: 'GET', body: '…' })`
        throws), and axios v1's XHR adapter silently drops the body
        on GET — both paths leave `resolve_patient_info` reading from
        an empty body and the matcher running with no patient context.
        Routing to `list` keeps the response shape stable; frontends
        that already speak `GET /trials/` keep working.
        """
        # Bind the action to 'list' so all the `self.action == 'list'`
        # branches in `get_queryset` fire — without this the queryset
        # would skip `with_potential_attrs_count` and `-match_score`
        # ordering.
        self.action = 'list'
        return self.list(request, *args, **kwargs)

    @action(methods=['post'], detail=True, url_path='match')
    def match_detail(self, request, *args, **kwargs):
        """POST alias for `retrieve`, carrying `patient_info` in the body.

        The detail endpoint needs patient context to render the eligibility
        table (`details.trialEligibilityAttributes` with the patient's
        `uvalue` / `matchingType`). `retrieve` is GET-only and GET-with-body
        is forbidden by the Fetch spec / silently dropped by axios's XHR
        adapter, so a host carrying an inline `patient_info` payload (the CB
        contract) can't reach it over GET. Mirror the list-level `match`
        action: bind to `retrieve` so `get_queryset` runs the retrieve
        annotations and `get_serializer_context` resolves the body's
        `patient_info`, then delegate. Hosts on the `?person_id=` path keep
        using plain `GET /trials/{pk}/`.
        """
        self.action = 'retrieve'
        return self.retrieve(request, *args, **kwargs)

    @action(methods=['get'], detail=False)
    def search(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            tab_counts = self._tab_counts()
            extra_keys = {} if tab_counts is None else {'tabCounts': tab_counts}
            return self.get_paginated_response(serializer.data, extra_keys=extra_keys)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def _tab_counts(self) -> Optional[dict]:
        """Totals for the tab bar, or None when they would be a lie.

        Deliberately computed here rather than at a `/trials/counts/`
        endpoint of its own. Counts are only meaningful under a patient
        context, and the only way to send one is a POST body — the
        `?person_id=` path is gated off outside DEBUG. A GET-only counts
        endpoint would answer every federated caller with
        `patient_info=None`, where `potential_attrs_count` collapses to
        `num_nonnulls(NULL)`: `potential` structurally zero and every row
        in the corpus labelled eligible, with nothing in the response to
        say so.

        Two cases return None instead of a plausible-looking number, for
        that same reason:

        - **No patient context.** Nothing has been judged, so calling the
          corpus `eligible` would assert a clinical verdict nobody made.
          `GET /trials/search/` can be called this way, so moving the
          counts onto it does not by itself fix what killed the endpoint.
        - **`?type=all`.** That routes through `filter_for_admin`, which
          skips the eligibility filter entirely; the rows are the corpus
          the caller asked for, but a per-row verdict was never computed.
          Listing them is honest, counting them as eligible is not.

        Otherwise the source is the annotated-but-not-type-narrowed
        queryset (`get_queryset`), run through the same `filter_queryset`
        the listed rows went through — so `?search=`, which DRF applies
        outside the matcher, lands on both — and the counts describe the
        whole matched corpus regardless of which tab is active.
        """
        source = getattr(self, '_tab_counts_source', None)
        if source is None:
            return None
        if getattr(self, '_tab_counts_patient_info', None) is None:
            return None
        if getattr(self, '_tab_counts_search_type', None) == 'all':
            return None

        source = self.filter_queryset(source)
        # `potential_attrs_count` is `num_nonnulls(...)`, never NULL, so the
        # two buckets partition the source exactly and the second is
        # arithmetic rather than a third pass over the corpus.
        total = source.count()
        eligible = source.filter(potential_attrs_count=0).count()
        return {'eligible': eligible, 'potential': total - eligible}

    @action(methods=['post'], detail=False, url_path='search/match',
            url_name='search-match')
    def search_match(self, request, *args, **kwargs):
        """POST alias for `search`, carrying `patient_info` in the body.

        The existing `match` alias routes to `list`, whose ordering is fixed
        at `-match_score, -posted_date, id`; only `search` reads `?sort=` and
        carries the tab counts. So a host holding an inline `patient_info` payload —
        the federated remote's default path, since GET-with-body is forbidden
        by the Fetch spec and dropped by axios's XHR adapter — could reach
        neither sorting nor counts at all.

        Binds `self.action = 'search'` so every `self.action == 'search'`
        branch in `get_queryset` fires, exactly as `match` does for `list`.
        """
        self.action = 'search'
        return self.search(request, *args, **kwargs)

    #: What a row of the export holds, as (header, key into the serialized
    #: trial). The keys are the serializer's own, so the file says what the
    #: list said — a column that drifts from the screen is worse than a column
    #: that is missing, because nobody checks it against anything.
    EXPORT_COLUMNS = (
        ('Trial ID', 'trialId'),
        ('Study ID', 'studyId'),
        ('Title', 'briefTitle'),
        ('Official title', 'officialTitle'),
        ('Match', 'matchingType'),
        ('Matching score', 'matchScore'),
        ('Suitability score', 'goodnessScore'),
        ('Status', 'recruitingStatus'),
        ('Phase', 'phase'),
        ('Trial type', 'trialType'),
        ('Disease', 'disease'),
        ('Stage', 'stage'),
        ('Treatments', 'interventionTreatments'),
        ('Sponsor', 'sponsor'),
        ('Locations', 'location'),
        ('Distance', 'distance'),
        ('Distance units', 'distanceUnits'),
        ('Enrolment', 'enrollmentCount'),
        ('Posted', 'postedDate'),
        ('Last update', 'lastUpdateDate'),
        ('First enrolment', 'firstEnrolment'),
        ('Link', 'link'),
    )

    #: Computed per row rather than read from the serializer, and only for the
    #: disease that has them. Appended after the columns above.
    EXPORT_MCL_COLUMN = 'High-risk MCL criteria'

    #: Rows fetched per round trip while streaming. Bounds the memory the
    #: response holds at once without making a query per row.
    EXPORT_CHUNK = 200

    #: The last line of a complete file, and of one that stopped early. The
    #: status code cannot carry either — it was sent before the first row.
    #: Comma-free on purpose: a sentinel the csv writer has to quote is one
    #: nobody can grep for.
    EXPORT_END_MARKER = '# end of export —'
    EXPORT_INCOMPLETE_MARKER = '# EXPORT INCOMPLETE — this file stopped early'

    def _reject_unnarrowed_export(self):
        """Refuse an export that names nothing to export.

        A search with no patient and no filters is a page of rows; the same
        export is the entire table, uncapped, once per request. The three
        things that make it a real question — a patient, a set of ids, or an
        explicit `type=all` — are exactly the three an intentional caller
        already sends, so this refuses the accident rather than the use.
        """
        if self.request.query_params.get('type') == 'all':
            return
        if self._resolve_trial_ids() is not None:
            return
        if self._resolve_patient_info() is not None:
            return
        raise serializers.ValidationError({
            'detail': [
                'An export needs something to narrow it: a patient '
                '(`patient_info` in the body, or `?person_id=`), a `trial_ids` '
                'list, or an explicit `?type=all` for the whole catalog.'
            ]
        })

    @action(methods=['post'], detail=False, url_path='export')
    def export(self, request, *args, **kwargs):
        """The current search, as a CSV file.

        POST, like every other action that needs a patient: GET-with-body is
        forbidden by the Fetch spec and dropped by axios, and the inline
        `patient_info` payload is the federated remote's default path. The
        cost is that the browser cannot simply follow a link — the caller
        reads the body and saves it — which is the same trade already made
        for `match` and `search-match`.

        Bound to `search`, so the file is the list: the same filters, the same
        `?sort=`, the same `trial_ids` narrowing for a Favorites export, and
        the same 400 on `type=favorites` / `type=my_trials` — those name a
        search type that narrows nothing here, and a CSV headed "your
        bookmarks" holding the whole corpus is the worst place to answer a
        question with the wrong set.

        Streamed rather than assembled: an unfiltered export is the whole
        corpus, and building that in memory to hand back at once is how an
        export endpoint takes a server down. Nothing is capped — a truncated
        file that does not say it was truncated reads as a complete one, and
        this is a file someone takes to an appointment. Which is also why the
        last line says the file ended: the response headers go out before the
        first row, so a failure halfway through arrives as a 200 with a short
        file, indistinguishable from a small result. The sentinel is how a
        reader — or a script — can tell the difference.
        """
        self.action = 'search'
        self._reject_unnarrowed_export()
        queryset = self.filter_queryset(self.get_queryset())
        # The locations are prefetched by the `search` branch; the trial type
        # is a plain FK the serializer reads per row, which on a page is one
        # extra query and on an uncapped export is one per trial.
        queryset = queryset.select_related('trial_type')
        # One serializer for the whole stream: `to_representation` reads only
        # `self.context`, and rebuilding the field map per trial is the
        # dominant cost on a corpus-sized export.
        context = self.get_serializer_context()
        # `explain` reaches this from the query string and would run
        # `TrialMatchExplainer` for every row — three queries each, measured —
        # to produce a `matchReasons` the CSV has no column for. On the one
        # endpoint that is uncapped by design, that is the per-row-query
        # problem walking back in through a parameter.
        context['explain'] = False
        serializer = self.get_serializer_class()(context=context)

        def rows():
            buffer = _io.StringIO()
            writer = csv.writer(buffer)

            def flush():
                value = buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
                return value

            writer.writerow(
                [header for header, _ in self.EXPORT_COLUMNS] + [self.EXPORT_MCL_COLUMN]
            )
            # A BOM, because the audience is someone opening this in Excel —
            # which ignores `charset=utf-8` on a saved file and decodes as the
            # system codepage, turning every accented sponsor and location into
            # mojibake. `utf-8-sig` on the reading side strips it.
            yield '\ufeff' + flush()

            patient_info = context.get('patient_info')
            written = 0
            try:
                for trial in queryset.iterator(chunk_size=self.EXPORT_CHUNK):
                    row = serializer.to_representation(trial)
                    writer.writerow(
                        [_export_cell(row.get(key)) for _, key in self.EXPORT_COLUMNS]
                        + [_export_cell(_export_mcl_cell(trial, patient_info))]
                    )
                    written += 1
                    yield flush()
            except Exception:
                # The 200 is already spent. All that is left is to say the file
                # is short — and to log it, since the client sees a success.
                logger.exception('trials export failed after %s rows', written)
                writer.writerow([f'{self.EXPORT_INCOMPLETE_MARKER} after {written} trials'])
                yield flush()
                return
            writer.writerow([f'{self.EXPORT_END_MARKER} {written} trials'])
            yield flush()

        filename = f'trials-{timezone.now():%Y-%m-%d}.csv'
        response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
        # `filename*` as well as `filename`: the name is ASCII today, and a
        # reader whose browser honours only one of the two still gets it.
        response['Content-Disposition'] = (
            f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'
        )
        return response


# ---------------------------------------------------------------------------
# Lookup table ViewSets
# ---------------------------------------------------------------------------

class CountriesViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CountrySerializer
    pagination_class = TrialsPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['title']

    def get_queryset(self):
        return PreferredCountry.objects.order_by(
            F('sort_key').asc(nulls_last=True), F('title')
        )


class LocationsViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LocationSerializer
    pagination_class = TrialsPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['title']

    def get_queryset(self):
        qs = Location.objects.select_related('country', 'state')
        country_id = self.request.query_params.get('country_id')
        state_id = self.request.query_params.get('state_id')
        if country_id:
            qs = qs.filter(country_id=country_id)
        if state_id:
            qs = qs.filter(state_id=state_id)
        return qs.order_by('title')


class FormSettingsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    DISEASE_NAME_TO_CODE = {
        'multiple myeloma': 'MM',
        'follicular lymphoma': 'FL',
        'breast cancer': 'BC',
        'chronic lymphocytic leukemia': 'CLL',
        'mantle cell lymphoma': 'MCL',
    }

    def list(self, request, *args, **kwargs):
        disease_param = request.query_params.get('disease', '')
        disease_code = self._normalize_disease_code(disease_param)
        value_options = ValueOptions()
        out = value_options.all_options()
        if disease_code:
            trial_types = ValueOptions.trial_types_by_disease_code(disease_code)
            out['trialType'] = {'options': ValueOptions.to_value_and_label(trial_types)}
            # Disease-aware treatment outcomes (#60 / #70 / CB #4137).
            # Without this override callers reading the union `therapyOutcome`
            # would still see IMWG-specific sCR / VGPR / MRD for BC patients.
            outcomes = value_options.therapy_outcomes_by_disease_code(disease_code)
            out['therapyOutcome'] = {'options': ValueOptions.to_value_and_label(outcomes)}
            # Per #63 / CB #4330: eight clinically disease-specific lists
            # were historically exposed as a single union to every patient.
            # Override each patient-facing union key with its per-disease
            # subset — key names match what `all_options()` already exposes
            # to the frontend (4 singular + 4 plural — preserved as-is for
            # back-compat). Trial-side `*Required` / `*Excluded` aliases
            # stay at the union (set by `trial_attributes.py` from a fresh
            # `ValueOptions().all_options()`), so trials can still require
            # any marker independent of the current patient's disease.
            disease_aware_overrides = {
                'flipiScore': value_options.flipi_scores_by_disease_code,
                'cytogenicMarkers': value_options.cytogenic_markers_by_disease_code,
                'molecularMarkers': value_options.molecular_markers_by_disease_code,
                'gelfCriteriaStatus': value_options.gelf_criteria_statuses_by_disease_code,
                'binetStages': value_options.binet_stages_by_disease_code,
                'richterTransformations': value_options.richter_transformations_by_disease_code,
                'tumorBurdens': value_options.tumor_burdens_by_disease_code,
                'diseaseActivities': value_options.disease_activities_by_disease_code,
            }
            for key, getter in disease_aware_overrides.items():
                out[key] = {'options': ValueOptions.to_value_and_label(getter(disease_code))}
        return Response(out)

    def _normalize_disease_code(self, disease_param: str) -> str:
        if not disease_param:
            return ''
        lower = disease_param.lower().strip()
        if lower.upper() in ('MM', 'BC', 'FL', 'CLL', 'MCL'):
            return lower.upper()
        return self.DISEASE_NAME_TO_CODE.get(lower, '')


class NormalizeCtomopRowView(APIView):
    """POST endpoint that exposes `normalize_ctomop_row` to authenticated
    callers. Takes a raw CTOMOP `patient_info` row in the body and
    returns the same row with EXACT-shaped values for the fields that
    differ between systems (receptor statuses → codes, TNM strings →
    short codes, therapy-line outcomes → IDs, refractory status labels,
    lab-value fallbacks, etc.).

    Exists so the federation dev harness (and any other client that
    fetches CTOMOP rows browser-side) can run the same normalization
    the server-side `?person_id=` resolver applies. Without this, an
    inline-fetch caller's `patient_info` reaches the matcher with raw
    CTOMOP labels and a meaningful subset of fields silently reads as
    "unknown" — closes the limitation documented in PR #117.

    Same auth + token model as `/trials/`: `IsAuthenticated`, DRF
    Token. The function is pure / side-effect-free; the caller already
    holds the patient row from their own session-authenticated CTOMOP
    fetch so this endpoint doesn't widen the IDOR surface tracked in
    #108.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from trials.services.patient_info.ctomop_adapter import normalize_ctomop_row

        raw = request.data
        if not isinstance(raw, dict):
            return Response(
                {'detail': 'Body must be a JSON object representing one CTOMOP patient_info row.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # `normalize_ctomop_row` mutates its argument in place; copy
        # first so we never alter caller-owned state. (`dict(raw)` is
        # a shallow copy — fine because the function only rewrites
        # top-level keys plus the `genetic_mutations` items, which the
        # function itself defensively copies via `m = dict(m)`.)
        normalized = normalize_ctomop_row(dict(raw))
        return Response(normalized)
