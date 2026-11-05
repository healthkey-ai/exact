"""
StudyPreferences — lightweight dataclass replacing the former StudyInfo DB model.

Fields are populated from API query parameters on each request; nothing is
persisted.  The interface is intentionally compatible with the StudyInfo object
that filtered_trials() / filter_by_study_info() previously consumed.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StudyPreferences:
    # Search text filters
    search_title: Optional[str] = None
    search_disease: Optional[str] = None
    search_treatment: Optional[str] = None
    # Intervention-arm filter: OMOP concept_ids of the therapy being studied.
    # Populated by CB; accepts ?therapy_id= one or more times.
    therapy_id: list[int] = field(default_factory=list)

    # Sponsor / registry filters
    sponsor: Optional[str] = None
    register: Optional[str] = None
    study_id: Optional[str] = None

    # Trial classification filters
    trial_type: Optional[str] = None
    # Several purposes at once (CB #4663), like therapy_id above: accepts
    # ?trialPurpose= one or more times. Empty means no filter.
    trial_purpose: list[str] = field(default_factory=list)
    study_type: Optional[str] = None

    # Recruitment filter
    recruitment_status: Optional[str] = None

    # Geographic / distance filters
    country: Optional[str] = None
    region: Optional[str] = None
    postal_code: Optional[str] = None
    distance: Optional[float] = None
    distance_units: str = 'km'

    # Quality filter
    validated_only: bool = False

    # Trial phase filter
    phase: Optional[str] = None

    # Date filters
    last_update: Optional[str] = None
    first_enrolment: Optional[str] = None


def study_preferences_from_query_params(params) -> StudyPreferences:
    """Build a StudyPreferences from DRF request.query_params (or any dict-like)."""

    def _float(key):
        val = params.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _bool(key, default=False):
        val = params.get(key)
        if val is None:
            return default
        return str(val).lower() in ('true', '1', 'yes')

    def _str(key):
        val = params.get(key)
        return val if val else None

    def _raw_list(key):
        if hasattr(params, 'getlist'):
            return params.getlist(key)
        if key in params:
            value = params[key]
            return value if isinstance(value, list) else [value]
        return []

    def _str_list(key, limit=50):
        """Repeated params and one comma-separated value both spell a list.

        Repetition is the query-string convention, and what CB's
        /trials/form-settings/ reads for its own `trial_purpose` param — but not
        every caller can emit it, so one comma-separated value is accepted too.
        Either way a caller holding the pre-#4663 single-value contract keeps
        working.

        Capped, because every code becomes another clause in the WHERE: the
        purpose taxonomy has nine entries, so a longer list is junk, and an
        unvalidated query string should not turn into thousands of them. CB
        bounds the same field at 50.
        """
        codes = [
            code.strip()
            for value in _raw_list(key)
            if value is not None  # or a dict-like caller's None becomes 'None'
            for code in str(value).split(',')
        ]
        # Deduplicated the way the queryset matches them — case-insensitively,
        # first spelling wins.
        result, seen = [], set()
        for code in codes:
            if code and code.casefold() not in seen:
                seen.add(code.casefold())
                result.append(code)
        return result[:limit]

    def _int_list(key):
        result = []
        for v in _raw_list(key):
            try:
                result.append(int(v))
            except (TypeError, ValueError):
                pass
        return result

    return StudyPreferences(
        search_title=_str('searchTitle'),
        search_disease=_str('searchDisease'),
        search_treatment=_str('searchTreatment'),
        therapy_id=_int_list('therapy_id'),
        sponsor=_str('sponsor'),
        register=_str('register'),
        study_id=_str('studyId'),
        trial_type=_str('trialType'),
        trial_purpose=_str_list('trialPurpose'),
        study_type=_str('studyType'),
        recruitment_status=_str('recruitmentStatus'),
        country=_str('country'),
        region=_str('region'),
        postal_code=_str('postalCode'),
        distance=_float('distance'),
        distance_units=params.get('distanceUnits', 'km') or 'km',
        validated_only=_bool('validatedOnly'),
        last_update=_str('lastUpdate'),
        first_enrolment=_str('firstEnrolment'),
    )
