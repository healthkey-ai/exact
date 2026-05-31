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

    # Sponsor / registry filters
    sponsor: Optional[str] = None
    register: Optional[str] = None
    study_id: Optional[str] = None

    # Trial classification filters
    trial_type: Optional[str] = None
    trial_purpose: Optional[str] = None
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

    return StudyPreferences(
        search_title=_str('searchTitle'),
        search_disease=_str('searchDisease'),
        search_treatment=_str('searchTreatment'),
        sponsor=_str('sponsor'),
        register=_str('register'),
        study_id=_str('studyId'),
        trial_type=_str('trialType'),
        trial_purpose=_str('trialPurpose'),
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
