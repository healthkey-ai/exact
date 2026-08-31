"""Differential-equivalence comparator: SQL classifier vs per-trial matcher (#4832).

Two code paths independently decide the same 3-valued eligibility for a
(patient, trial) pair, and they can disagree:

- SQL path  — `filter_by_patient_info` (the hard eligibility filter) plus
  `with_potential_attrs_count` (the `potential_attrs_count` annotation).
  A trial is SQL ``not_eligible`` when the filter drops it, ``eligible`` when it
  survives with ``potential_attrs_count == 0``, and ``potential`` otherwise.
- matcher path — `UserToTrialAttrMatcher.trial_match_status()`.

This module measures the disagreement. It does NOT reconcile it — that is the
fix tracked in #4832. The reconcile contract is *equality* (not a one-way
superset): for every (patient, trial),

    sql_status == matcher_status

Anything less either mislabels an eligible trial as potential (wrong tab /
matchScore / attributesToFillIn) or silently drops a trial the patient
qualifies for (a false negative in a clinical-trial matcher). See the #4832
thread for why the superset was rejected.

``compare()`` returns one `Divergence` per disagreeing trial, annotated with the
matcher's deciding attrs and the SQL filter's drop attribution, so the diverging
attribute *class* is visible on real data.

Scope: this isolates the patient-vs-matcher equivalence — it applies
`filter_by_patient_info` but NOT `filter_by_study_info` (recruitment status,
distance, validated-only). The production `list` endpoint drops more trials for
those study reasons, so the measured rate is a patient-eligibility discrepancy,
not a count of what a real tab would show.

Cost: `compare()` runs the per-trial Python matcher over the WHOLE corpus (that
is what detecting a divergence requires), and the matcher issues a per-trial
`Therapy` query — an N+1 across the corpus. It is an offline measurement tool,
not a request-path call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trials.services.attribute_names import AttributeNames
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
from trials.services.utils import disease_attr_applies

# The 3-valued verdict, shared by both paths.
ELIGIBLE = 'eligible'
POTENTIAL = 'potential'
NOT_ELIGIBLE = 'not_eligible'


@dataclass
class Divergence:
    """One (patient, trial) pair where the SQL and matcher verdicts disagree."""
    trial_id: int
    sql_status: str
    matcher_status: str
    #: matcher criteria that came back 'unknown' (drive matcher 'potential').
    matcher_unknown_attrs: list[str] = field(default_factory=list)
    #: matcher criteria that came back 'not_matched' (drive matcher 'not_eligible').
    matcher_not_matched_attrs: list[str] = field(default_factory=list)
    #: SQL hard-filter drop attribution — the FIRST disqualifying attr. The filter
    #: is sequential and `add_traces` counts the drop the moment the scope hits 0,
    #: so exactly one attr shows here (unlike the matcher side, which lists all
    #: not_matched attrs). Read it as "first disqualifier", not the full set.
    sql_dropped_attrs: list[str] = field(default_factory=list)
    #: SQL potential attribution (attributesToFillIn), when the SQL path marked it
    #: potential. May be EMPTY even though the SQL count said potential: the count
    #: (`potential_attrs_to_check`) and the attribution (`potential_attrs_for_trial`)
    #: are separate code and can disagree — an empty list here on a `potential->*`
    #: divergence is itself signal (the SQL count can't name what it counted).
    sql_potential_attrs: list[str] = field(default_factory=list)

    @property
    def direction(self) -> str:
        """`sql->matcher`, e.g. ``eligible->potential`` — the shape of the disagreement."""
        return f'{self.sql_status}->{self.matcher_status}'


def sql_status_map(base_qs, patient_info) -> dict[int, str]:
    """Map every trial id in ``base_qs`` to its SQL verdict for this patient.

    ``base_qs`` is the pre-patient trial set (already scoped by disease/study if
    desired). A trial dropped by ``filter_by_patient_info`` is ``not_eligible``;
    a survivor is ``eligible`` iff ``potential_attrs_count == 0``.
    """
    base_ids = set(base_qs.values_list('id', flat=True))

    filtered, _ = base_qs.filter_by_patient_info(patient_info)
    annotated = filtered.with_potential_attrs_count(patient_info)
    surviving = {
        row['id']: row['potential_attrs_count']
        for row in annotated.values('id', 'potential_attrs_count')
    }

    out: dict[int, str] = {}
    for tid in base_ids:
        if tid not in surviving:
            out[tid] = NOT_ELIGIBLE
        elif surviving[tid] == 0:
            out[tid] = ELIGIBLE
        else:
            out[tid] = POTENTIAL
    return out


def matcher_status(trial, patient_info) -> tuple[str, list[str], list[str]]:
    """Return ``(verdict, unknown_attrs, not_matched_attrs)`` for one trial.

    Mirrors `trial_match_status`'s aggregation (not_matched wins, then unknown),
    but also surfaces the deciding attrs so the diverging class is legible. The
    disease gate matches the matcher's own (`trial_match_status`): criteria not
    applicable to the trial's disease never contribute.
    """
    matcher = UserToTrialAttrMatcher(trial, patient_info)
    unknown_attrs: list[str] = []
    not_matched_attrs: list[str] = []
    for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items():
        # Mirror trial_match_status's gate exactly: `disease_attr_applies`, not a
        # substring `in` test. A bare-string `disease` (most config entries) would
        # substring-match under `in` ('M' in 'MM') and drift from the real matcher.
        if 'disease' in meta and (
            matcher.disease_code is None
            or not disease_attr_applies(meta['disease'], matcher.disease_code)
        ):
            continue
        status = matcher.attr_match_status(attr)
        if status == 'not_matched':
            not_matched_attrs.append(attr)
        elif status == 'unknown':
            unknown_attrs.append(attr)

    if not_matched_attrs:
        verdict = NOT_ELIGIBLE
    elif unknown_attrs:
        verdict = POTENTIAL
    else:
        verdict = ELIGIBLE
    return verdict, unknown_attrs, not_matched_attrs


def _sql_dropped_attrs(base_qs, trial_id, patient_info) -> list[str]:
    """Which SQL hard-filter attrs dropped this trial (for attribution)."""
    _, traces = base_qs.filter(id=trial_id).filter_by_patient_info(
        patient_info, add_traces=True
    )
    return [
        str(t['attr']).replace('patient_info.', '')
        for t in traces
        if t.get('dropped', 0) > 0
    ]


def _blank_attr_counts(patient_info) -> dict:
    """The blank-attribute set the SQL potential annotation keys on.

    `potential_attrs_to_check` returns the blank (unfilled) user attrs; feeding
    their keys as ``counts`` to `attrs_to_fill_in` reproduces the per-trial SQL
    potential attribution (`potential_attrs_for_trial` iterates ``counts``).
    """
    attrs2check = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info)
    return {user_attr: 1 for user_attr in attrs2check}


def _sql_potential_attrs(trial, counts) -> list[str]:
    """Which attrs the SQL path would list as attributesToFillIn for this trial.

    `attrs_to_fill_in` emits camelCase `userAttributeName`; normalize back to the
    snake_case mapping keys so this shares one namespace with the matcher and
    drop attributions (the `by_attr` histogram buckets all four together).
    """
    return [
        AttributeNames.get_by_camel_case(entry['userAttributeName'])
        for entry in trial.attrs_to_fill_in(counts)
        if entry
    ]


def compare(base_qs, patient_info, attribute_sql_drops: bool = True) -> list[Divergence]:
    """Compare the SQL and matcher verdicts across ``base_qs`` for one patient.

    Returns one `Divergence` per trial whose verdicts disagree. When
    ``attribute_sql_drops`` is set, divergent trials the SQL path excluded are
    re-probed with ``add_traces=True`` to name the dropping attrs (cheap: only
    the divergent subset is re-probed).
    """
    sql_map = sql_status_map(base_qs, patient_info)

    # Only the base ids the SQL map knows about; fetch trials once for the matcher.
    trials = {t.id: t for t in base_qs}
    counts = _blank_attr_counts(patient_info) if attribute_sql_drops else {}

    divergences: list[Divergence] = []
    for tid, sql_verdict in sql_map.items():
        trial = trials.get(tid)
        if trial is None:
            continue
        m_verdict, unknown_attrs, not_matched_attrs = matcher_status(trial, patient_info)
        if m_verdict == sql_verdict:
            continue
        d = Divergence(
            trial_id=tid,
            sql_status=sql_verdict,
            matcher_status=m_verdict,
            matcher_unknown_attrs=unknown_attrs,
            matcher_not_matched_attrs=not_matched_attrs,
        )
        if attribute_sql_drops and sql_verdict == NOT_ELIGIBLE:
            d.sql_dropped_attrs = _sql_dropped_attrs(base_qs, tid, patient_info)
        elif attribute_sql_drops and sql_verdict == POTENTIAL:
            d.sql_potential_attrs = _sql_potential_attrs(trial, counts)
        divergences.append(d)
    return divergences


@dataclass
class ComparisonSummary:
    """Aggregate divergence stats over one or many patient runs."""
    trials_compared: int = 0
    divergences: int = 0
    by_direction: dict[str, int] = field(default_factory=dict)
    #: attr -> count, over the deciding attrs of every divergence.
    by_attr: dict[str, int] = field(default_factory=dict)

    def add(self, trials_compared: int, divs: list[Divergence]) -> None:
        self.trials_compared += trials_compared
        self.divergences += len(divs)
        for d in divs:
            self.by_direction[d.direction] = self.by_direction.get(d.direction, 0) + 1
            deciding = (d.matcher_not_matched_attrs or d.matcher_unknown_attrs
                        or d.sql_dropped_attrs or d.sql_potential_attrs)
            for a in deciding:
                self.by_attr[a] = self.by_attr.get(a, 0) + 1

    @property
    def rate(self) -> float:
        return (self.divergences / self.trials_compared) if self.trials_compared else 0.0
