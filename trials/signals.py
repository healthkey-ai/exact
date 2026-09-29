# PatientInfo is no longer persisted — no pre_save normalization needed.
# Normalization is called explicitly in resolve.py (_build_in_memory).

from django.core.signals import request_finished, request_started

from exact_matching.omop.languages_match_profile import reset_languages_decision

# One EXACT_OMOP_LANGUAGES readiness decision per request (CB #5350): reset at both
# ends so a decision never leaks into the next request or into non-request work.
request_started.connect(reset_languages_decision, dispatch_uid='exact_omop_languages_decision_start')
request_finished.connect(reset_languages_decision, dispatch_uid='exact_omop_languages_decision_finish')
