import itertools
import re
from typing import TYPE_CHECKING

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.contrib.postgres.search import SearchVector, SearchQuery

from django.db import models
from django.db.models import Case, Count, Q, When, Exists, OuterRef, Value, QuerySet, Min, Subquery
from django.db.models.functions import Coalesce, Least
from django.contrib.gis.geos import Point
from django.db.models import BigIntegerField, F, FloatField, ExpressionWrapper, IntegerField, RawSQL

from django.utils import timezone

import datetime as dt

from trials.enums import PriorTherapyLines
from trials.services.patient_info.configs import (
    USER_TO_TRIAL_ATTRS_MAPPING,
    THERAPY_LINES_ATTRS_UNDERSCORED,
    ATTR_MAPPING_TYPE_COMPUTED, SCT_HISTORY_EXCLUDED_MAPPING,
    sct_value_is_none,
)
from trials.services.receptor_hierarchy import expand_values as expand_receptor_values
from trials.services.patient_info.genetic_mutations import GeneticMutations
from trials.services.patient_info.patient_info_flipi_score import PatientInfoFlipyScore
from trials.services.trial_details.configs import PHASE_CODE_MAPPING
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
from trials.services.utils import disease_attr_applies


if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.models import Trial


def cast_str_to_int(value):
    if value is None:
        return value

    if type(value) == int:
        return value

    if type(value) == str and value.isdigit():
        return int(value)


def get_recruitment_status_filter_values(recruitment_status):
    """
    Returns a list of recruitment status values to filter by, or None if no filter should be applied.

    This helper ensures consistent filtering logic for both Trial.recruitment_status
    and LocationTrial.recruitment_status.

    Rules:
    - None, '', 'ALL' → None (no filter)
    - 'RECRUITING' → ['RECRUITING']
    - 'RECRUITING_AND_NOT_YET_RECRUITING' → ['RECRUITING', 'NOT_YET_RECRUITING']
    - Other values → [value.upper()]
    """
    if recruitment_status is None or str(recruitment_status) == '':
        return None

    status = recruitment_status.upper()
    if status == "ALL":
        return None
    elif status == "RECRUITING":
        return ["RECRUITING"]
    elif status == "RECRUITING_AND_NOT_YET_RECRUITING":
        return ["RECRUITING", "NOT_YET_RECRUITING"]
    else:
        return [status]


# ---------------------------------------------------------------------------
# Dispatch table for filter_by_patient_info's custom_search branch (#29).
#
# `filter_by_patient_info` used to be a ~110-line if/elif chain on
# user_attr — adding any new patient attr meant grepping the whole method,
# inserting a branch, and praying the dispatch order didn't matter. The
# table below collapses that into data: per-user_attr handler callables
# that take `(scope, value, ctx)` and return the new scope. Handlers
# that need shared state (therapy-line tracking, patient_info access)
# mutate ctx in place.
# ---------------------------------------------------------------------------


def _csv(value):
    return value.split(",") if value else []


def _csv_stripped(value):
    return [x.strip() for x in _csv(value)]


def _filter_disease(scope, value, _ctx):
    return scope.filter(disease__iexact=value.lower())


def _filter_tumor_grade(scope, value, _ctx):
    return scope.eligible_for_tumor_grade(str(value).lower())


def _filter_prior_therapy(scope, value, ctx):
    scope = scope.eligible_for_prior_therapy(value)
    # Apply the therapy-related-things filter ONLY ONCE per
    # filter_by_patient_info call; ctx tracks the flag.
    if ctx['has_no_prior_therapy'] and not ctx['is_therapies_filter_applied']:
        scope = scope.eligible_for_therapy_related_things_from_lines(
            ctx['user_therapies'], ctx['has_no_prior_therapy']
        )
        ctx['is_therapies_filter_applied'] = True
    return scope


def _filter_concomitant_medications(scope, value, ctx):
    return scope.eligible_for_concomitant_medications_and_washout_period(
        value, ctx['patient_info'].concomitant_medication_date
    )


def _filter_last_treatment(scope, value, ctx):
    if not ctx['has_no_prior_therapy']:
        return scope.eligible_for_washout_period_duration(value)
    return scope


def _filter_therapy_lines_once(scope, _value, ctx):
    # Used for therapy-line attrs (first_line_therapy, second_line_therapy,
    # later_therapy, later_therapies, supportive_therapies) — the actual
    # filter is applied once at most across all of them.
    if not ctx['is_therapies_filter_applied']:
        scope = scope.eligible_for_therapy_related_things_from_lines(
            ctx['user_therapies'], ctx['has_no_prior_therapy']
        )
        ctx['is_therapies_filter_applied'] = True
    return scope


_CUSTOM_SEARCH_DISPATCH = {
    # Stateful / special handlers.
    'disease': _filter_disease,
    'tumor_grade': _filter_tumor_grade,
    'prior_therapy': _filter_prior_therapy,
    'concomitant_medications': _filter_concomitant_medications,
    'last_treatment': _filter_last_treatment,
    # Pass-through.
    'stage': lambda s, v, _c: s.eligible_for_stage(v),
    'flipi_score_options': lambda s, v, _c: s.eligible_for_flipi_score_options(v),
    'genetic_mutations': lambda s, v, _c: s.eligible_for_genetic_mutations(v),
    'plasma_cell_leukemia': lambda s, v, _c: s.eligible_for_plasma_cell_leukemia(v),
    'progression': lambda s, v, _c: s.eligible_for_progression(v),
    'treatment_refractory_status': lambda s, v, _c: s.eligible_for_treatment_refractory_status(v),
    'pre_existing_condition_categories': lambda s, v, _c: s.eligible_for_pre_existing_condition(v),
    'stem_cell_transplant_history': lambda s, v, _c: s.eligible_for_stem_cell_transplant_history(v),
    'bcl2_inhibitor_refractory': lambda s, v, _c: s.eligible_for_bcl2_inhibitor_refractory(v),
    'btk_inhibitor_refractory': lambda s, v, _c: s.eligible_for_btk_inhibitor_refractory(v),
    'tp53_disruption': lambda s, v, _c: s.eligible_for_tp53_disruption(v),
    # Comma-separated.
    'ethnicity': lambda s, v, _c: s.eligible_for_ethnicity(_csv(v)),
    'languages_skills': lambda s, v, _c: s.eligible_for_languages_skills(_csv(v)),
    'planned_therapies': lambda s, v, _c: s.eligible_for_planned_therapies(_csv(v)),
    'cytogenic_markers': lambda s, v, _c: s.eligible_for_cytogenic_markers(_csv(v)),
    'molecular_markers': lambda s, v, _c: s.eligible_for_molecular_marker(_csv(v)),
    'histologic_type': lambda s, v, _c: s.eligible_for_histologic_types(_csv(v)),
    'estrogen_receptor_status': lambda s, v, _c: s.eligible_for_estrogen_receptor_statuses(_csv(v)),
    'progesterone_receptor_status': lambda s, v, _c: s.eligible_for_progesterone_receptor_statuses(_csv(v)),
    'her2_status': lambda s, v, _c: s.eligible_for_her2_statuses(_csv(v)),
    'hrd_status': lambda s, v, _c: s.eligible_for_hrd_statuses(_csv(v)),
    'hr_status': lambda s, v, _c: s.eligible_for_hr_statuses(_csv(v)),
    'tumor_stage': lambda s, v, _c: s.eligible_for_tumor_stages(_csv(v)),
    'nodes_stage': lambda s, v, _c: s.eligible_for_nodes_stages(_csv(v)),
    'distant_metastasis_stage': lambda s, v, _c: s.eligible_for_distant_metastasis_stages(_csv(v)),
    'staging_modalities': lambda s, v, _c: s.eligible_for_staging_modalities(_csv(v)),
    'protein_expressions': lambda s, v, _c: s.eligible_for_protein_expressions(_csv(v)),
    # Comma-separated + per-value strip.
    'richter_transformation': lambda s, v, _c: s.eligible_for_richter_transformations(_csv_stripped(v)),
    'tumor_burden': lambda s, v, _c: s.eligible_for_tumor_burdens(_csv_stripped(v)),
    'disease_activity': lambda s, v, _c: s.eligible_for_disease_activities(_csv_stripped(v)),
    'binet_stage': lambda s, v, _c: s.eligible_for_binet_stages(_csv_stripped(v)),
    # MCL (#94). Single-string patient attrs are CSV-split for parity with
    # the matcher's COMPUTED branch at user_to_trial_attr_matcher.py:592-594
    # — without that, a `'classic,leukemic'` value would matcher-overlap as
    # two codes but queryset-filter on the literal joined string, silently
    # dropping every trial. Patient list attrs flow through unchanged.
    'morphologic_variant': lambda s, v, _c: s.eligible_for_morphologic_variants(_csv_stripped(v)),
    'disease_behavior': lambda s, v, _c: s.eligible_for_disease_behaviors(_csv_stripped(v)),
    'disease_subtype': lambda s, v, _c: s.eligible_for_disease_subtypes(_csv_stripped(v)),
    'mipi_risk': lambda s, v, _c: s.eligible_for_mipi_risks(_csv_stripped(v)),
    'mipi_c_risk': lambda s, v, _c: s.eligible_for_mipi_c_risks(_csv_stripped(v)),
    'extranodal_sites': lambda s, v, _c: s.eligible_for_extranodal_sites(v),
    'bulky_disease_criteria': lambda s, v, _c: s.eligible_for_bulky_disease_criteria(v),
}


class TrialQuerySet(models.QuerySet):
    def recruiting(self):
        return self.filter(recruitment_status='RECRUITING')

    def recruiting_and_not_yet_recruiting(self):
        return self.filter(recruitment_status__in=['RECRUITING', 'NOT_YET_RECRUITING'])

    # UserTrial removed — no favorites/participation tracking in standalone app

    def add_potential_attrs_count(self, attributes, search_type=None, counts=None, filled_attributes=None):
        """Annotate trials with potential_attrs_count, match_score, and
        potential_profit_avg from `num_nonnulls()` of the candidate CASE
        expressions.

        The fragments in `attributes` / `filled_attributes` come from
        `UserToTrialAttrsMapper.potential_attrs_to_check` — values are
        SQL CASE-WHEN strings built from the static
        `USER_TO_TRIAL_ATTRS_MAPPING` (no user-supplied SQL). Migrating
        from the deprecated `.extra(select=…)/extra(where=…)` to
        `annotate(RawSQL(…))` + filter-on-annotation (#97) — same SQL
        emitted, same return shape.
        """
        if filled_attributes is None:
            filled_attributes = []

        attrs = ','.join(attributes + ['NULL'])
        filled_attrs = ','.join(filled_attributes + ['NULL'])
        all_attrs = ','.join(attributes + filled_attributes + ['NULL'])

        # (filled / filled + potential) * 100 — integer division, capped at 100.
        # NULLIF guards against div-by-zero on the `patient_info=None` path
        # where `all_attrs` collapses to just `'NULL'` and num_nonnulls = 0
        # (latent crash in the pre-port `.extra()` form too — closed here
        # since the new test surface exercises that path).
        match_score_sql = (
            f'num_nonnulls({filled_attrs}) * 100 / '
            f'NULLIF(num_nonnulls({all_attrs}), 0)'
        )

        sql_conditions = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info=None, counts=counts)
        sql_conditions_sum = ' + '.join([*sql_conditions.values(), '0'])

        # BigIntegerField on potential_profit_avg: the numerator sums
        # arbitrary counts across the catalog and can exceed 2^31 in the
        # large-counts case (same rationale as #25 / PR #98).
        query = self.annotate(
            potential_attrs_count=RawSQL(
                f'num_nonnulls({attrs})', [], output_field=IntegerField()
            ),
            match_score=RawSQL(
                match_score_sql, [], output_field=IntegerField()
            ),
            potential_profit_avg=RawSQL(
                f'({sql_conditions_sum}) / NULLIF(num_nonnulls({attrs}), 0)',
                [], output_field=BigIntegerField(),
            ),
        )

        if search_type == 'eligible':
            query = query.filter(potential_attrs_count=0)
        elif search_type == 'potential':
            query = query.filter(potential_attrs_count__gt=0)
        return query

    def filtered_trials(self, search_options, study_info, patient_info, add_traces=False, search_type=None):

        # - USER attribute NOT NULL, TRIAL attribute NOT NULL - Trial is either
        #   ELIGIBLE or NOT ELIGIBLE based on values of this attribute
        #   (combined with other matching attributes)
        #
        # - USER attribute NOT NULL, TRIAL attribute NULL - Trial may ELIGIBLE or not
        #   based on OTHER attributes
        #
        # - USER attribute NULL, TRIAL attribute NULL - it may be ELIGIBLE
        #   based on other attribute matches
        #
        # - USER attribute NULL, TRIAL attribute NOT NULL, it is POTENTIAL
        #   and the attribute should be added to list of what makes it go
        #   from POTENTIAL to ELIGIBLE

        query = self

        if search_type in ['all', 'favorites', 'my_trials']:
            query, _ = query.filter_for_admin(study_info, patient_info)
            max_distance = D(mi=1000)
            if study_info.distance:
                max_distance = D(mi=study_info.distance) if str(study_info.distance_units).lower() == 'miles' else D(
                    km=study_info.distance)
            query = query.with_distance_optimized(
                geo_point=patient_info.geo_point if patient_info else None,
                max_distance=max_distance,
                recruitment_status=study_info.recruitment_status
            )

            return query, []

        else:
            query, study_traces = query.filter_by_study_info(study_info, add_traces, user_geo_point=patient_info.geo_point if patient_info else None)
            query, patient_traces = query.filter_by_patient_info(patient_info, add_traces)

            return query, list(study_traces) + list(patient_traces)

    def filter_for_admin(self, study_info, patient_info):
        traces = []
        if not study_info:
            return self, traces

        query = self

        query = query.by_titles(study_info.search_title)
        query = query.by_study_id(study_info.study_id)
        query = query.by_register(study_info.register)
        query = query.by_trial_type(study_info.trial_type)
        query = query.by_trial_purpose(study_info.trial_purpose)
        query = query.by_study_type(study_info.study_type)
        query = query.by_validated_only(study_info.validated_only)
        if patient_info and patient_info.disease is not None and patient_info.disease != '':
            query = query.filter(disease__icontains=patient_info.disease.lower())

        query = query.order_by('is_validated')

        return query, traces

    def filter_by_study_info(self, study_info, add_traces=False, user_geo_point=None):
        traces = []
        if not study_info:
            return self, traces

        query = self
        count = 0

        if add_traces:
            count = query.count()

        query = query.by_titles(study_info.search_title)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.search_title',
                'val': study_info.search_title,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_intervention_treatment(study_info.search_treatment)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.search_treatment',
                'val': study_info.search_treatment,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_register(study_info.register)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.register',
                'val': study_info.register,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_trial_type(study_info.trial_type)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.trial_type',
                'val': study_info.trial_type,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_trial_purpose(study_info.trial_purpose)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.trial_purpose',
                'val': study_info.trial_purpose,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_study_type(study_info.study_type)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.study_type',
                'val': study_info.study_type,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_sponsor(study_info.sponsor)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.sponsor',
                'val': study_info.sponsor,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_last_update(study_info.last_update)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.last_update',
                'val': study_info.last_update,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_first_enrolment_date(study_info.first_enrolment)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.first_enrolment',
                'val': study_info.first_enrolment,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        if user_geo_point and study_info.distance and study_info.distance_units:
            max_distance = D(mi=1000)
            if study_info.distance:
                max_distance = D(mi=study_info.distance) if study_info.distance_units.lower() == 'miles' else D(
                    km=study_info.distance)
            query = query.with_distance_optimized(
                geo_point=user_geo_point,
                max_distance=max_distance,
                recruitment_status=study_info.recruitment_status
            )
            query = query.by_distance(user_geo_point, study_info.distance, study_info.distance_units)
            if add_traces:
                new_count = query.count()
                traces.append({
                    'attr': 'user_geo_point, study_info.distance, study_info.distance_units',
                    'val': f"user_geo_point.coords: '{user_geo_point.coords if user_geo_point else None}', distance: '{study_info.distance}', distance_units: '{study_info.distance_units}'",
                    'records': new_count,
                    'dropped': count-new_count
                })
                count = new_count
        else:
            query = query.by_location(study_info.country, study_info.region)
            if add_traces:
                new_count = query.count()
                traces.append({
                    'attr': 'study_info.country, study_info.region',
                    'val': f"country: '{study_info.country}', region: '{study_info.region}'",
                    'records': new_count,
                    'dropped': count-new_count
                })
                count = new_count

        query = query.by_recruitment_status(study_info.recruitment_status)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.recruitment_status',
                'val': study_info.recruitment_status,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_validated_only(study_info.validated_only)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.validated_only',
                'val': study_info.validated_only,
                'records': new_count,
                'dropped': count-new_count
            })
            count = new_count

        query = query.by_phase(study_info.phase)
        if add_traces:
            new_count = query.count()
            traces.append({
                'attr': 'study_info.phase',
                'val': study_info.phase,
                'records': new_count,
                'dropped': count-new_count
            })

        return query, traces

    def by_study_id(self, study_id):
        if not study_id:
            return self

        return self.filter(study_id=study_id)

    def by_titles(self, search_title):
        if not search_title or search_title == '':
            return self

        # https://docs.djangoproject.com/en/5.1/ref/contrib/postgres/search/#full-text-search
        return self.annotate(
            search=SearchVector("brief_title", "official_title"),
        ).filter(
            Q(search=SearchQuery(search_title, search_type="plain")) |
            Q(brief_title__icontains=search_title) |
            Q(official_title__icontains=search_title)
        )

    def by_intervention_treatment(self, search_treatment):
        if not search_treatment or search_treatment == '':
            return self

        query = search_treatment.lower()
        query = f"'{query}'"
        query = query.replace(' and not ', "' & !'")
        query = query.replace(' not ', "' & !'")
        if query.startswith("'not "):
            query = query.replace("'not ", "!'")
        query = query.replace(' and ', "' & '")
        query = query.replace(' or ', "' | '")

        # https://docs.djangoproject.com/en/5.1/ref/contrib/postgres/search/#full-text-search
        return self.annotate(
            search=SearchVector("intervention_treatments_text"),
        ).filter(
            search=SearchQuery(query, search_type="raw")
        )

    def by_register(self, register):
        return self.eligible_for_str_value('register', register, allow_blank=False)

    def by_trial_type(self, trial_type):
        return self.eligible_for_relation('trial_type__code', trial_type)

    def by_trial_purpose(self, trial_purpose):
        """Filter by Trial.purpose. Accepts a code string or a TrialPurpose
        instance (uses its `.code`); blank/None is a no-op.
        """
        if trial_purpose is None or trial_purpose == '':
            return self
        code = getattr(trial_purpose, 'code', trial_purpose)
        return self.eligible_for_relation('purpose__code', code)

    def by_study_type(self, study_type):
        if not study_type or str(study_type).upper() in ('', 'ALL'):
            return self
        if study_type.upper() == 'INTERVENTIONAL_AND_OBSERVATIONAL':
            return self.filter(study_type__in=['INTERVENTIONAL', 'OBSERVATIONAL'])
        return self.filter(study_type__iexact=study_type)

    def by_phase(self, phase):
        if phase in PHASE_CODE_MAPPING:
            phase_code = PHASE_CODE_MAPPING.index(phase)
            return self.filter(phase_code_min__gte=phase_code)
        return self

    def by_sponsor(self, value):
        if value is None or str(value) == '':
            return self

        return self.filter(sponsor_name__icontains=str(value).lower())

    def by_location(self, country, state):
        from trials.models import Country, State

        if not state and not country:
            return self

        if country:
            country_by_name = Country.objects.filter(title__iexact=country).first()
            if not country_by_name:
                return self

            if state:
                state_by_name = State.objects.filter(country=country_by_name, title__iexact=state).first()
                if state_by_name:
                    return self.filter(locationtrial__location__state_id=state_by_name.id).distinct()

            return self.filter(locationtrial__location__country_id=country_by_name.id).distinct()

        return self

    def by_distance(self, geo_point, distance, distance_units):
        if not geo_point or not distance or distance <= 0 or not distance_units:  # skip
            return self

        if hasattr(self.query, 'annotations') and 'distance' in self.query.annotations:
            max_distance = D(mi=distance) if distance_units.lower() == 'miles' else D(km=distance)

            return self.filter(
                has_nearby_location=True,
                distance__lte=max_distance
            )

        return self

    def with_distance_optimized_old(self, geo_point, max_distance=None):
        if not geo_point:
            return self

        from trials.models import LocationTrial
        from django.db.models import Subquery, OuterRef, Value, Case, When, IntegerField
        from django.contrib.gis.db.models.functions import Distance

        location_qs = LocationTrial.objects.filter(
            trial=OuterRef('pk')
        )

        if max_distance is not None:
            location_qs = location_qs.filter(
                location__geo_point__dwithin=(geo_point, max_distance)
            )

        location_qs = location_qs.annotate(
            dist=Distance('location__geo_point', geo_point)
        ).order_by('dist')

        qs = self.annotate(
            distance=Subquery(location_qs.values('dist')[:1]),
            is_null_distance=Case(
                When(distance__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).distinct()

        return qs

    def with_distance_optimized(self, geo_point, max_distance=None, recruitment_status=None):
        if not geo_point:
            return self

        from trials.models import LocationTrial
        from django.db.models import Subquery, OuterRef, Value, Case, When, IntegerField, Min, Exists
        from django.contrib.gis.db.models.functions import Distance

        # Get recruitment status filter values (if any)
        status_values = get_recruitment_status_filter_values(recruitment_status)

        nearby_exists = LocationTrial.objects.filter(
            trial=OuterRef('pk'),
            location__geo_point__isnull=False
        )

        if status_values is not None:
            nearby_exists = nearby_exists.filter(recruitment_status__in=status_values)

        if max_distance is not None:
            nearby_exists = nearby_exists.filter(
                location__geo_point__dwithin=(geo_point, max_distance)
            )

        min_distance_subquery = LocationTrial.objects.filter(
            trial=OuterRef('pk')
        )

        if status_values is not None:
            min_distance_subquery = min_distance_subquery.filter(recruitment_status__in=status_values)

        if max_distance is not None:
            min_distance_subquery = min_distance_subquery.filter(
                location__geo_point__dwithin=(geo_point, max_distance)
            )

        min_distance_subquery = min_distance_subquery.annotate(
            dist=Distance('location__geo_point', geo_point)
        ).values('trial').annotate(
            min_dist=Min('dist')
        ).values('min_dist')

        qs = self.annotate(
            has_nearby_location=Exists(nearby_exists),
            distance=Subquery(min_distance_subquery)
        ).annotate(
            distance_available=Case(
                When(distance__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField()
            )
        ).distinct()

        return qs

    def by_last_update(self, value):
        return self.by_date_since('last_update_date', value)

    def by_first_enrolment_date(self, value):
        return self.by_date_since('first_enrolment_date', value)

    def by_date_since(self, attr_name, value):
        since_in_years = cast_str_to_int(value)

        if not since_in_years:
            return self

        days = 365 * since_in_years
        date_since = dt.datetime.now() - dt.timedelta(days=days)

        return self.filter(
            Q(**{f'{attr_name}__gte': date_since}) | Q(**{f'{attr_name}__isnull': True})
        )

    def by_recruitment_status(self, recruitment_status):
        if recruitment_status is None or str(recruitment_status) == '':
            return self

        status = recruitment_status.upper()
        if status == "ALL":
            return self
        elif status == "RECRUITING":
            return self.filter(recruitment_status="RECRUITING")
        elif status == "RECRUITING_AND_NOT_YET_RECRUITING":
            return self.filter(recruitment_status__in=["RECRUITING", "NOT_YET_RECRUITING"])

        return self.eligible_for_str_value('recruitment_status', recruitment_status.upper())

    def by_validated_only(self, validated_only):
        if validated_only is not True:
            return self

        return self.filter(is_validated=True)

    def with_potential_attrs_count(self, patient_info, search_type=None, counts=None):
        if patient_info is None:
            return self.add_potential_attrs_count([], search_type, counts=counts)

        if counts is None:
            counts = {}

        attrs2check, filled_attrs2check = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info, with_eligible=True)
        return self.add_potential_attrs_count(list(attrs2check.values()), search_type, counts=counts, filled_attributes=list(filled_attrs2check.values()))

    def with_status_code(self):
        return self.annotate(
            status_code=Case(
                When(recruitment_status='RECRUITING', then=1),
                When(recruitment_status='NOT_YET_RECRUITING', then=2),
                default=3
            )
        )

    def with_goodness_score_optimized(self,
                                        benefit_weight: float = 25.0,
                                        patient_burden_weight: float = 25.0,
                                        risk_weight: float = 25.0,
                                        distance_penalty_weight: float = 25.0,
                                        geo_point=None,
                                        recruitment_status=None) -> QuerySet["Trial"]:
        from django.db.models import F, ExpressionWrapper, Value, FloatField, Subquery, OuterRef, Min
        from django.db.models.functions import Cast, Least, Coalesce

        meters_per_200_miles = 200 * 1609.34

        if not geo_point:
            distance_expr = Value(0.0)
        else:
            from trials.models import LocationTrial
            from django.contrib.gis.db.models.functions import Distance

            status_values = get_recruitment_status_filter_values(recruitment_status)

            min_dist_sq = LocationTrial.objects.filter(
                trial=OuterRef('pk'),
                location__geo_point__isnull=False,
            )
            if status_values is not None:
                min_dist_sq = min_dist_sq.filter(recruitment_status__in=status_values)
            min_dist_sq = min_dist_sq.annotate(
                dist=Distance('location__geo_point', geo_point)
            ).values('trial').annotate(
                min_dist=Min('dist')
            ).values('min_dist')

            distance_expr = Least(
                ExpressionWrapper(
                    Cast(Subquery(min_dist_sq), output_field=FloatField()) / meters_per_200_miles,
                    output_field=FloatField()
                ),
                Value(1.0),
                output_field=FloatField()
            )

        weights_sum = benefit_weight + patient_burden_weight + risk_weight + distance_penalty_weight

        max_benefit = Value(20.0, output_field=FloatField())
        max_burden = Value(20.0, output_field=FloatField())
        max_risk = Value(20.0, output_field=FloatField())

        # score = (Wb×Benefit/20 + Wpb×(1−PB/20) + Wr×(1−Risk/20) + Wd×(1−DistancePenalty)) × 100 / ΣW + 0.5
        # Wb / Wpb / Wr / Wd are the caller-supplied benefit / patient-burden /
        # risk / distance weights (default 25/25/25/25 — equal). +0.5 is a
        # rounding nudge before the IntegerField cast.
        score_expr = ExpressionWrapper(
            (
                    benefit_weight * Coalesce(F('benefit_score'), Value(0.0)) / max_benefit +
                    patient_burden_weight * (1 - Coalesce(F('patient_burden_score'), max_burden) / max_burden) +
                    risk_weight * (1 - Coalesce(F('risk_score'), max_risk) / max_risk) +
                    distance_penalty_weight * (1 - distance_expr)
            ) * 100 / weights_sum + 0.5,
            output_field=IntegerField()
        )

        return self.annotate(
            goodness_score=score_expr,
        )

    def eligible_for_min_max_value(self, attr_min_name, attr_max_name, value, skip_blank=True):
        if value is None:
            return self
        if skip_blank and value == 0:
            return self

        scope = self
        if attr_min_name is not None:
            scope = scope.filter(
                Q(**{f'{attr_min_name}__lte': value}) | Q(**{f'{attr_min_name}__isnull': True})
            )
        if attr_max_name is not None:
            scope = scope.filter(
                Q(**{f'{attr_max_name}__gte': value}) | Q(**{f'{attr_max_name}__isnull': True})
            )

        return scope

    def eligible_for_bool_value(self, attr_name, value):
        if value is None:
            return self

        return self.filter(
            Q(**{attr_name: value}) | Q(**{f'{attr_name}__isnull': True})
        )

    def eligible_for_value(self, attr_name, value):
        return self.filter(
            Q(**{attr_name: value}) | Q(**{f'{attr_name}__isnull': True})
        )

    def eligible_for_computed_value(self, attr_name, value):
        return self.filter(
            Q(**{attr_name: value}) | Q(**{f'{attr_name}__isnull': True})
        )

    def eligible_for_relation(self, attr_name, value):
        if value is None or value == '':
            return self

        cond = Q(**{f'{attr_name}__iexact': value})
        return self.filter(cond)

    def eligible_for_str_value(self, attr_name, value, allow_blank=True):
        if value is None or value == '':
            return self

        if allow_blank:
            cond = Q(**{f'{attr_name}__iexact': value}) | Q(**{attr_name: ''}) | Q(**{f'{attr_name}__isnull': True})
        else:
            cond = Q(**{f'{attr_name}__iexact': value})
        return self.filter(cond)

    def eligible_for_inversed_bool_restriction_value(self, attr_name, value):
        if value is True:
            return self.filter(~Q(**{attr_name: value}))

        return self

    def eligible_for_bool_requirement_value(self, attr_name, value, is_under_user_control=False):
        if is_under_user_control:
            return self

        if value is None:
            value = False

        if value is False:
            return self.filter(Q(**{attr_name: value}) | Q(**{f'{attr_name}__isnull': True}))

        return self

    def eligible_for_stage(self, stage):
        if stage is None or stage == '':
            return self
        # Also match parent stage in case patient has a sub-stage (e.g. IIIB → III).
        # Normalization in _normalize_ctomop_row strips sub-stages at load time, but
        # this makes the filter robust for any remaining sub-stage values.
        parent_stage = re.sub(r'[A-C]$', '', stage)
        q = Q(stages__contains=stage) | Q(stages=[])
        if parent_stage != stage:
            q |= Q(stages__contains=parent_stage)
        return self.filter(q)

    def eligible_for_tumor_grade(self, tumor_grade):
        attr_min_name = 'tumor_grade_min'
        attr_max_name = 'tumor_grade_max'

        if tumor_grade.isdigit():
            return self.eligible_for_min_max_value(attr_min_name, attr_max_name, int(tumor_grade))

        from trials.services.value_options import ValueOptions
        mapping = {}
        for k, v in ValueOptions().tumor_grades().items():
            mapping[v.lower()] = k

        tumor_grade_val = str(tumor_grade).lower()
        int_val = mapping.get(tumor_grade_val, None)
        if int_val:
            return self.eligible_for_min_max_value(attr_min_name, attr_max_name, int_val)

        return self

    def eligible_for_flipi_score_options(self, flipi_score_options):
        attr_min_name = 'flipi_score_min'
        attr_max_name = 'flipi_score_max'
        flipi_score = PatientInfoFlipyScore.scope_by_options(flipi_score_options)
        return self.eligible_for_min_max_value(attr_min_name, attr_max_name, flipi_score)

    def eligible_for_prior_therapy(self, prior_therapy):
        if prior_therapy is None or prior_therapy == '':
            return self

        # "None", "One line", "Two lines", "More than two lines of therapy"
        raw_value = str(prior_therapy).lower()

        if raw_value == 'none':
            int_value = 0
        elif raw_value == 'one line':
            int_value = 1
        elif raw_value == 'two lines':
            int_value = 2
        elif raw_value == 'more than two lines of therapy':
            int_value = 3
        else:
            int_value = None

        return self.eligible_for_min_max_value('therapy_lines_count_min', 'therapy_lines_count_max', int_value, skip_blank=False)

    def eligible_for_genetic_mutations(self, genetic_mutations):
        if genetic_mutations == []:
            return self

        genes = GeneticMutations.mutation_genes(genetic_mutations)
        scope = self.eligible_for_required_lists(
            values=genes,
            required_attr_name='mutation_genes_required',
            allow_empty_list=False
        )

        variants = GeneticMutations.mutation_variants(genetic_mutations)
        scope = scope.eligible_for_required_lists(
            values=variants,
            required_attr_name='mutation_variants_required',
            allow_empty_list=False
        )

        origins = GeneticMutations.mutation_origins(genetic_mutations)
        scope = scope.eligible_for_required_lists(
            values=origins,
            required_attr_name='mutation_origins_required',
            allow_empty_list=False
        )

        interpretations = GeneticMutations.mutation_interpretations(genetic_mutations)
        scope = scope.eligible_for_required_lists(
            values=interpretations,
            required_attr_name='mutation_interpretations_required',
            allow_empty_list=False
        )

        return scope

    def eligible_for_plasma_cell_leukemia(self, plasma_cell_leukemia):
        if plasma_cell_leukemia is None:
            return self

        if plasma_cell_leukemia is True:
            return self.exclude(no_plasma_cell_leukemia_required=True)
        elif plasma_cell_leukemia is False:
            return self.exclude(plasma_cell_leukemia_required=True)

        return self

    def eligible_for_progression(self, progression):
        if progression is None or str(progression) == '':
            return self

        if progression == 'active':
            return self.exclude(disease_progression_smoldering_required=True)
        elif progression == 'smoldering':
            return self.exclude(disease_progression_active_required=True)

        return self

    def eligible_for_treatment_refractory_status(self, treatment_refractory_status):
        if treatment_refractory_status is None or str(treatment_refractory_status) == '':
            return self

        #  "notRefractory": "Not Refractory (progression halted)",
        #  "primaryRefractory": "Primary Refractory",
        #  "secondaryRefractory": "Secondary Refractory",
        #  "multiRefractory": "Multi-Refractory",
        if treatment_refractory_status in ['notRefractory', 'Not Refractory (progression halted)']:
            return self.exclude(refractory_required=True)

        return self.exclude(not_refractory_required=True)

    def eligible_for_required_lists(self, values: list[str], required_attr_name: str, allow_empty_list: bool = True) -> models.QuerySet:
        if values is None:
            return self

        if allow_empty_list and values == []:
            return self

        values = [str(x).strip() for x in values]

        return self.filter(
            Q(**{f'{required_attr_name}__has_any_keys': values}) | Q(**{f'{required_attr_name}__exact': []})
        )

    def eligible_for_required_and_excluded_lists(self, values: list[str], required_attr_name: str, excluded_attr_name: str) -> models.QuerySet:
        if values is None or values == []:
            return self

        values = [str(x).strip() for x in values]

        return self.filter(
            Q(**{f'{required_attr_name}__has_any_keys': values}) | Q(**{f'{required_attr_name}__exact': []})
        ).exclude(
            Q(**{f'{excluded_attr_name}__has_any_keys': values})
        )

    def eligible_for_therapy_related_things_from_lines(self, therapy_codes: list[str], has_no_prior_therapy=False) -> models.QuerySet:
        if has_no_prior_therapy:
            return self.filter(therapies_required__exact=[], therapy_components_required__exact=[], therapy_types_required__exact=[])

        if therapy_codes is None or therapy_codes == []:
            return self

        from trials.models import TherapyComponent, TherapyComponentCategory

        scope = self.eligible_for_therapy_from_lines(therapy_codes)

        components = TherapyComponent.objects.filter(therapycomponentconnection__therapy__code__in=therapy_codes).all()
        component_codes = [x.code for x in components]

        if len(component_codes) > 0:
            scope = scope.eligible_for_therapy_components(component_codes)

        categories = TherapyComponentCategory.objects.filter(
            therapycomponentcategoryconnection__component__in=components).all()
        therapy_types = [x.code for x in categories]

        if len(therapy_types) > 0:
            scope = scope.eligible_for_therapy_types(therapy_types)
        return scope

    def eligible_for_therapy_from_lines(self, therapy_codes: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=therapy_codes,
            required_attr_name='therapies_required',
            excluded_attr_name='therapies_excluded'
        )

    def eligible_for_therapy_components(self, therapy_component_codes: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=therapy_component_codes,
            required_attr_name='therapy_components_required',
            excluded_attr_name='therapy_components_excluded'
        )

    def eligible_for_therapy_types(self, therapy_type_codes: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=therapy_type_codes,
            required_attr_name='therapy_types_required',
            excluded_attr_name='therapy_types_excluded'
        )

    def eligible_for_pre_existing_condition(self, pre_existing_conditions: list[str]) -> models.QuerySet:
        if pre_existing_conditions is None or pre_existing_conditions == []:
            return self

        pre_existing_conditions = [str(x).strip() for x in pre_existing_conditions]

        return self.exclude(pre_existing_conditions_excluded__has_any_keys=pre_existing_conditions)

    def eligible_for_stem_cell_transplant_history(self, stem_cell_transplant_history) -> models.QuerySet:
        if stem_cell_transplant_history is None or str(stem_cell_transplant_history) == '':
            return self

        # See sct_value_is_none() — tolerates both storage shapes
        # ('None' bare string from signal cleanup; ['None'] list from
        # the multiselect) and whitespace/casing variants (#4333, #4340).
        if sct_value_is_none(stem_cell_transplant_history):
            return self.exclude(stem_cell_transplant_history_required=True)

        if not isinstance(stem_cell_transplant_history, (list, tuple)):
            stem_cell_transplant_history = [x.strip() for x in str(stem_cell_transplant_history).split(',')]

        mapped_items = []
        for item in stem_cell_transplant_history:
            for rec in SCT_HISTORY_EXCLUDED_MAPPING.get(item, [item]):
                mapped_items.append(rec)

        mapped_items = list(set(mapped_items))

        return self.exclude(stem_cell_transplant_history_excluded__has_any_keys=mapped_items)

    def eligible_for_concomitant_medications_and_washout_period(self, concomitant_medications, concomitant_medication_date) -> models.QuerySet:
        if concomitant_medications is None or str(concomitant_medications) == '':
            return self

        if str(concomitant_medications).lower() == 'none':
            return self

        if not isinstance(concomitant_medications, (list, tuple)):
            concomitant_medications = [x.strip() for x in str(concomitant_medications).split(',')]

        if concomitant_medication_date:
            current_washout_period_in_days = (dt.date.today() - concomitant_medication_date).days
            return self.exclude(
                Q(concomitant_medications_excluded__has_any_keys=concomitant_medications) &
                (
                        Q(concomitant_medications_washout_period_duration__gt=current_washout_period_in_days) |
                        Q(concomitant_medications_washout_period_duration__isnull=True)
                )
            )

        return self.exclude(concomitant_medications_excluded__has_any_keys=concomitant_medications)

    def eligible_for_washout_period_duration(self, last_treatment) -> models.QuerySet:
        if last_treatment is None:
            return self

        current_washout_period_in_days = (dt.date.today() - last_treatment).days
        return self.filter(
            Q(washout_period_duration__lt=current_washout_period_in_days) | Q(washout_period_duration__isnull=True)
        )

    def eligible_for_planned_therapies(self, planned_therapies: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=planned_therapies,
            required_attr_name='planned_therapies_required',
            excluded_attr_name='planned_therapies_excluded'
        )

    def eligible_for_supportive_therapies(self, supportive_therapies: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=supportive_therapies,
            required_attr_name='supportive_therapies_required',
            excluded_attr_name='supportive_therapies_excluded'
        )

    def eligible_for_cytogenic_markers(self, cytogenic_markers: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=cytogenic_markers,
            required_attr_name='cytogenic_markers_required',
            excluded_attr_name='cytogenic_markers_excluded'
        )

    def eligible_for_molecular_marker(self, molecular_markers: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=molecular_markers,
            required_attr_name='molecular_markers_required',
            excluded_attr_name='molecular_markers_excluded'
        )

    def eligible_for_histologic_types(self, histologic_types: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=histologic_types,
            required_attr_name='histologic_types_required'
        )

    def eligible_for_ethnicity(self, ethnicities: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=ethnicities,
            required_attr_name='ethnicity_required'
        )

    def eligible_for_languages_skills(self, languages_skills: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=languages_skills,
            required_attr_name='languages_skills_required'
        )

    # Receptor parent-code expansion lives in trials.services.receptor_hierarchy
    # — shared with the matcher's uvalue_function lambdas so SQL filtering and
    # match-status display stay in sync.

    def eligible_for_estrogen_receptor_statuses(self, estrogen_receptor_statuses: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=expand_receptor_values(estrogen_receptor_statuses, 'er'),
            required_attr_name='estrogen_receptor_statuses_required'
        )

    def eligible_for_progesterone_receptor_statuses(self, progesterone_receptor_statuses: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=expand_receptor_values(progesterone_receptor_statuses, 'pr'),
            required_attr_name='progesterone_receptor_statuses_required'
        )

    def eligible_for_her2_statuses(self, her2_statuses: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=her2_statuses,
            required_attr_name='her2_statuses_required'
        )

    def eligible_for_hrd_statuses(self, hrd_statuses: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=hrd_statuses,
            required_attr_name='hrd_statuses_required'
        )

    def eligible_for_hr_statuses(self, hr_statuses: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=expand_receptor_values(hr_statuses, 'hr'),
            required_attr_name='hr_statuses_required'
        )

    def eligible_for_tumor_stages(self, tumor_stages: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=tumor_stages,
            required_attr_name='tumor_stages_required',
            excluded_attr_name='tumor_stages_excluded'
        )

    def eligible_for_nodes_stages(self, nodes_stages: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=nodes_stages,
            required_attr_name='nodes_stages_required',
            excluded_attr_name='nodes_stages_excluded'
        )

    def eligible_for_distant_metastasis_stages(self, distant_metastasis_stages: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=distant_metastasis_stages,
            required_attr_name='distant_metastasis_stages_required',
            excluded_attr_name='distant_metastasis_stages_excluded'
        )

    def eligible_for_staging_modalities(self, staging_modalities: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=staging_modalities,
            required_attr_name='staging_modalities_required'
        )

    def eligible_for_protein_expressions(self, protein_expressions: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=protein_expressions,
            required_attr_name='protein_expressions_required',
            excluded_attr_name='protein_expressions_excluded'
        )

    def eligible_for_richter_transformations(self, richter_transformations: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=richter_transformations,
            required_attr_name='richter_transformations_required',
            excluded_attr_name='richter_transformations_excluded'
        )

    def eligible_for_tumor_burdens(self, tumor_burdens: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=tumor_burdens,
            required_attr_name='tumor_burdens_required'
        )

    def eligible_for_bcl2_inhibitor_refractory(self, bcl2_inhibitor_refractory: bool) -> models.QuerySet:
        if bcl2_inhibitor_refractory is None:
            return self
        if bcl2_inhibitor_refractory is True:
            return self.exclude(bcl2_inhibitor_refractory_excluded=True)
        else:
            return self.exclude(bcl2_inhibitor_refractory_required=True)

    def eligible_for_btk_inhibitor_refractory(self, btk_inhibitor_refractory: bool) -> models.QuerySet:
        if btk_inhibitor_refractory is None:
            return self
        if btk_inhibitor_refractory is True:
            return self.exclude(btk_inhibitor_refractory_excluded=True)
        else:
            return self.exclude(btk_inhibitor_refractory_required=True)

    def eligible_for_disease_activities(self, disease_activities: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=disease_activities,
            required_attr_name='disease_activities_required'
        )

    def eligible_for_binet_stages(self, binet_stages: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=binet_stages,
            required_attr_name='binet_stages_required'
        )

    # ------------------------------------------------------------------
    # MCL filters (#94). The patient-side attrs land here as either a
    # single string (variant / risk / behavior / subtype) or a list of
    # strings (extranodal_sites / bulky_disease_criteria). All map to
    # JSON-list trial columns checked via the shared list helpers above.
    # ------------------------------------------------------------------

    def eligible_for_morphologic_variants(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_and_excluded_lists(
            values=values,
            required_attr_name='morphologic_variants_required',
            excluded_attr_name='morphologic_variants_excluded',
        )

    def eligible_for_disease_behaviors(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='disease_behaviors_required',
        )

    def eligible_for_disease_subtypes(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='disease_subtypes_required',
        )

    def eligible_for_extranodal_sites(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='extranodal_sites_required',
        )

    def eligible_for_mipi_risks(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='mipi_risks_required',
        )

    def eligible_for_mipi_c_risks(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='mipi_c_risks_required',
        )

    def eligible_for_bulky_disease_criteria(self, values: list[str]) -> models.QuerySet:
        return self.eligible_for_required_lists(
            values=values,
            required_attr_name='bulky_disease_criteria_required',
        )

    def eligible_for_tp53_disruption(self, tp53_disruption: bool) -> models.QuerySet:
        """
        Filter trials based on TP53 disruption status.
        - If trial requires TP53 disruption (required=True) and patient doesn't have it, exclude
        - If trial excludes TP53 disruption (excluded=True) and patient has it, exclude
        """
        if tp53_disruption is None:
            return self

        if tp53_disruption is True:
            # Patient has TP53 disruption
            # Exclude trials that exclude TP53 disruption
            return self.exclude(tp53_disruption_excluded=True)
        else:
            # Patient does not have TP53 disruption
            # Exclude trials that require TP53 disruption
            return self.exclude(tp53_disruption_required=True)

    def eligible_for_researcher_email(self, email) -> models.QuerySet:
        if email is None:
            return self.none()
        return self.filter(researchers_emails__has_any_keys=[email.lower()])

    def filter_by_patient_info(self, patient_info, add_traces=False):
        from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

        traces = []
        if patient_info is None:
            return self, traces

        scope = self
        count = 0

        if add_traces:
            count = scope.count()

        mapping = USER_TO_TRIAL_ATTRS_MAPPING
        patient_info_attr = PatientInfoAttributes(patient_info)

        has_no_prior_therapy = patient_info.prior_therapy in ["None"]
        user_therapies = patient_info_attr.get_user_therapies()
        is_therapies_filter_applied = False


        for user_attr in mapping.keys():
            user_attr_value = patient_info_attr.get_value(user_attr)
            if patient_info_attr.is_attr_blank(user_attr):
                continue

            trial_attr_meta = mapping[user_attr]

            if 'computed_value_type' in trial_attr_meta:
                user_attr_type = trial_attr_meta['computed_value_type']
            elif user_attr == 'pre_existing_condition_categories':
                user_attr_type = list
            else:
                user_attr_type = type(patient_info.__class__._meta.get_field(user_attr))

            allow_blank_values = trial_attr_meta.get("allow_blank_values", False)
            if not allow_blank_values:
                if user_attr_type in ['int', models.fields.IntegerField] and user_attr_value == 0:
                    continue
                if user_attr_type in ['float', models.fields.DecimalField] and user_attr_value == 0.0:
                    continue
                if user_attr_type in ['float', models.fields.FloatField] and user_attr_value == 0.0:
                    continue

            # do the search now
            trial_attr_name = trial_attr_meta["attr"]
            if "disease" in trial_attr_meta and (
                patient_info_attr.disease_code is None
                or not disease_attr_applies(trial_attr_meta["disease"], patient_info_attr.disease_code)
            ):
                continue

            if "custom_search" in trial_attr_meta and trial_attr_meta["custom_search"] is True:
                ctx = {
                    'patient_info': patient_info,
                    'has_no_prior_therapy': has_no_prior_therapy,
                    'user_therapies': user_therapies,
                    'is_therapies_filter_applied': is_therapies_filter_applied,
                }
                handler = _CUSTOM_SEARCH_DISPATCH.get(user_attr)
                if handler is not None:
                    scope = handler(scope, user_attr_value, ctx)
                elif user_attr in [*THERAPY_LINES_ATTRS_UNDERSCORED, 'supportive_therapies']:
                    scope = _filter_therapy_lines_once(scope, user_attr_value, ctx)
                else:
                    raise Exception(
                        f'type "{trial_attr_meta["type"]}" is not supported for user_attr "{user_attr}"'
                    )
                is_therapies_filter_applied = ctx['is_therapies_filter_applied']

            elif trial_attr_meta["type"] == "value":
                scope = scope.eligible_for_value(trial_attr_name, user_attr_value)

            elif trial_attr_meta["type"] == ATTR_MAPPING_TYPE_COMPUTED:
                for current_trial_attr_name in trial_attr_name:
                    function = trial_attr_meta["uvalue_function"][current_trial_attr_name]
                    user_attr_value = function(patient_info)
                    scope = scope.eligible_for_value(current_trial_attr_name, user_attr_value)

            elif trial_attr_meta["type"] == "str_value":
                scope = scope.eligible_for_str_value(trial_attr_name, user_attr_value)

            elif trial_attr_meta["type"] == "bool_restriction":
                under_user_control = "under_user_control" in trial_attr_meta and trial_attr_meta["under_user_control"] is True
                scope = scope.eligible_for_bool_requirement_value(trial_attr_name, user_attr_value, under_user_control)

            elif trial_attr_meta["type"] == "inversed_bool_restriction":
                scope = scope.eligible_for_inversed_bool_restriction_value(trial_attr_name, user_attr_value)

            elif trial_attr_meta["type"] == "min_value":
                if 'attr_min' in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'
                scope = scope.eligible_for_min_max_value(attr_min_name, None, user_attr_value, skip_blank=not allow_blank_values)

            elif trial_attr_meta["type"] == "max_value":
                if 'attr_max' in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'
                scope = scope.eligible_for_min_max_value(None, attr_max_name, user_attr_value, skip_blank=not allow_blank_values)

            elif trial_attr_meta["type"] == "min_max_value":
                if "attr_min" in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'
                if "attr_max" in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'
                scope = scope.eligible_for_min_max_value(attr_min_name, attr_max_name, user_attr_value, skip_blank=not allow_blank_values)

                user_attr_value_uln = patient_info_attr.get_uln_value(user_attr)
                if user_attr_value_uln:
                    uln_attr_min_name = trial_attr_meta["uln_attr_min"]
                    uln_attr_max_name = trial_attr_meta["uln_attr_max"]
                    scope = scope.eligible_for_min_max_value(uln_attr_min_name, uln_attr_max_name, user_attr_value_uln)

            else:
                raise Exception(f'type "{trial_attr_meta["type"]}" is not supported')

            if add_traces:
                new_count = scope.count()
                traces.append({
                    'attr': f'patient_info.{user_attr}',
                    'val': user_attr_value,
                    'records': new_count,
                    'dropped': count-new_count
                })
                count = new_count
        return scope, traces
