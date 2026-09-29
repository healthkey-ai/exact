"""Asked-state language verdict on the OMOP path (CB #5350).

State model: trials/services/omop/patient_languages.py. The worked examples run
through the queryset (SQL) and the per-trial matcher, and status_equivalence.compare
must report no divergence for them (it compares verdicts only). The language
share of the potential count, the match-score numerator and attrs-to-fill-in are
tested separately for cases 3 and 4.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import Trial
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.matching import status_equivalence as se
from trials.services.omop.patient_languages import LANGUAGE_CAPABILITY_FIELDS
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

EN, ES = '4180186', '4182511'
EN_SPEAK, ES_SPEAK = f'{EN}:2100007853', f'{ES}:2100007853'


@pytest.fixture(autouse=True)
def omop_ready(db, settings):
    settings.EXACT_OMOP_LANGUAGES = True
    LoadLangOptions().load_all()
    call_command('load_language_omop_concept_ids', stdout=StringIO())


def _trial(*codes):
    pairs = {'speak__en': EN_SPEAK, 'speak__es': ES_SPEAK}
    return TrialFactory(disease='multiple myeloma', languages_skills_required=list(codes),
                        omop_languages_skills_required=sorted(pairs[c] for c in codes))


def _patient(**booleans):
    row = {'disease': 'Multiple Myeloma', 'patient_age': 60}
    row.update({name: None for name in LANGUAGE_CAPABILITY_FIELDS})
    row.update(booleans)
    return build_patient_info_from_promop_row(row)


def _status(trial, patient):
    return UserToTrialAttrMatcher(trial, patient).attr_match_status('languages_skills')


def _kept(trial, patient):
    scope, _ = Trial.objects.filter(id=trial.id).filter_by_patient_info(patient)
    return scope.exists()


def _lang_potential(trial, patient):
    from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
    attrs2check = UserToTrialAttrsMapper().potential_attrs_to_check(patient)
    if 'languages_skills' not in attrs2check:
        return False
    sql = attrs2check['languages_skills']
    from django.db.models.expressions import RawSQL
    from django.db.models import IntegerField
    row = Trial.objects.filter(id=trial.id).annotate(
        p=RawSQL(f'num_nonnulls({sql})', [], output_field=IntegerField())).values('p').first()
    return row['p'] == 1


EXAMPLES = [
    # name, trial codes, patient booleans, status, kept
    ('anna', ('speak__en',), {'english_speak': True}, 'matched', True),
    ('boris', ('speak__en',), {}, 'unknown', True),
    ('carlos', ('speak__en',), {'english_speak': False, 'english_read': True}, 'not_matched', False),
    ('carlos_es', ('speak__es',), {'english_speak': False, 'english_read': True}, 'unknown', True),
    ('en_or_es_asked_en', ('speak__en', 'speak__es'), {'english_read': True}, 'unknown', True),
    ('en_or_es_asked_both', ('speak__en', 'speak__es'),
     {'english_read': True, 'spanish_write': False}, 'not_matched', False),
    ('all_false', ('speak__en',), {name: False for name in LANGUAGE_CAPABILITY_FIELDS}, 'not_matched', False),
    ('no_requirement', (), {'english_speak': False, 'english_read': False}, 'matched', True),
]


@pytest.mark.parametrize('name, codes, booleans, status, kept', EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_worked_example(name, codes, booleans, status, kept):
    trial, patient = _trial(*codes), _patient(**booleans)
    assert _status(trial, patient) == status
    assert _kept(trial, patient) is kept


def test_case_4_is_potential_and_offers_languages():
    # Carlos vs a Spanish requirement: English asked only -> potential, fill in languages
    trial, patient = _trial('speak__es'), _patient(english_speak=False, english_read=True)
    assert _lang_potential(trial, patient)
    counts = {'languages_skills': 1}
    assert any(item['userAttributeName'] == 'languagesSkills' for item in trial.attrs_to_fill_in(counts))


def test_case_3_is_not_potential():
    trial, patient = _trial('speak__en'), _patient(english_speak=True)
    assert not _lang_potential(trial, patient)


def test_status_equivalence_has_no_divergence_over_the_examples():
    trials = [_trial(*codes) for _, codes, _, _, _ in EXAMPLES]
    base = Trial.objects.filter(id__in=[t.id for t in trials])
    patients = {name: _patient(**booleans) for name, _, booleans, _, _ in EXAMPLES}
    for name, patient in patients.items():
        assert se.compare(base, patient) == [], name


@pytest.mark.parametrize('required', ['speak__es', 'write__en'])
def test_direct_pairs_are_never_not_matched(required):
    # a client sending pairs only proves no negative, not even for another skill in
    # the same language
    pairs = {'speak__es': ES_SPEAK, 'write__en': f'{EN}:2100007855'}
    trial = TrialFactory(disease='multiple myeloma', languages_skills_required=[required],
                         omop_languages_skills_required=[pairs[required]])
    patient = PatientInfo(disease='multiple myeloma', patient_age=60, language_skill_concept_ids=[EN_SPEAK])
    assert _status(trial, patient) == 'unknown'
    assert _kept(trial, patient)
    assert _lang_potential(trial, patient)
    assert se.compare(Trial.objects.filter(id=trial.id), patient) == []


def test_asked_list_is_built_only_for_languages_with_a_concept_id():
    patient = _patient(english_speak=False)
    assert patient.language_asked_concept_ids == [EN]
    assert patient.language_skill_concept_ids == []


@pytest.mark.parametrize('value, asked', [
    (False, [EN]), (0, [EN]), ('false', [EN]), (' F ', [EN]), ('no', [EN]), ('0', [EN]),
    (None, []), ('', []), ('maybe', []), (2, []),
])
def test_false_parsing_marks_the_language_asked(value, asked):
    assert _patient(english_speak=value).language_asked_concept_ids == asked


def test_hostile_values_do_not_reach_sql():
    trial = _trial('speak__en')
    patient = PatientInfo(disease='multiple myeloma',
                          language_skill_concept_ids=["1:2') OR 1=1 --"],
                          language_asked_concept_ids=["4180186'); DROP TABLE x; --", EN])
    # the shaped value EN counts; the hostile ones are dropped from the SQL arrays
    assert _kept(trial, patient) is False
    assert _status(trial, patient) == 'not_matched'


def test_fill_in_languages_only_where_the_language_is_open():
    # English speaker: a Spanish trial (unknown) asks for languages, an English
    # trial already satisfied does not, even when both are potential for other reasons
    patient = _patient(english_speak=True)
    es_trial, en_trial = _trial('speak__es'), _trial('speak__en')
    counts = {'languages_skills': 1}

    def asks(trial):
        return any(i and i['userAttributeName'] == 'languagesSkills'
                   for i in trial.attrs_to_fill_in(counts, patient_info=patient))
    assert asks(es_trial) is True
    assert asks(en_trial) is False
    assert any(i and i['userAttributeName'] == 'languagesSkills'
               for i in en_trial.attrs_to_fill_in(counts))  # no patient: as before


def test_pairs_only_patient_matched_on_overlap_is_eligible_everywhere():
    trial = _trial('speak__en')
    patient = PatientInfo(disease='multiple myeloma', patient_age=60, language_skill_concept_ids=[EN_SPEAK])
    assert _status(trial, patient) == 'matched'
    assert not _lang_potential(trial, patient)
    assert se.compare(Trial.objects.filter(id=trial.id), patient) == []


# ── count, match score and attrs-to-fill-in for cases 3 and 4 ─────────

def _annotated(trial, patient):
    return (Trial.objects.filter(id=trial.id).with_potential_attrs_count(patient)
            .values('potential_attrs_count', 'match_score').first())


def _real_counts(patient, trials):
    from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount
    return BlankAttributeRecordsCount().counts(Trial.objects.filter(id__in=[t.id for t in trials]), patient)


def _asks_languages(trial, counts, patient):
    return any(i and i['userAttributeName'] == 'languagesSkills'
               for i in trial.attrs_to_fill_in(counts, patient_info=patient))


def test_case_3_and_case_4_on_count_score_and_fill_in():
    patient = _patient(english_speak=True)                  # asked English, speaks it
    none, case3, case4 = _trial(), _trial('speak__en'), _trial('speak__es')
    base = _annotated(none, patient)
    c3, c4 = _annotated(case3, patient), _annotated(case4, patient)
    # potential count: case 4 adds exactly the language attr, case 3 adds nothing
    assert c4['potential_attrs_count'] == base['potential_attrs_count'] + 1
    assert c3['potential_attrs_count'] == base['potential_attrs_count']
    # match score: case 3 adds a satisfied attr, case 4 an open one
    assert c3['match_score'] >= base['match_score']
    assert c4['match_score'] < c3['match_score']
    counts = _real_counts(patient, [none, case3, case4])
    assert counts.get('languages_skills', 0) >= 1
    assert _asks_languages(case4, counts, patient) is True
    assert _asks_languages(case3, counts, patient) is False


# ── pk__in subquery: the condition binds to the inner alias ───────────

def test_condition_is_not_correlated_inside_a_pk_in_subquery():
    trial = _trial('speak__en')
    inner = Trial.objects.eligible_for_languages_skills_omop([], [EN]).values('pk')
    outer = Trial.objects.filter(pk__in=inner)
    inner_where = str(outer.query).split(' U0 WHERE ', 1)[1]  # the subquery's own condition
    assert '"trials_trial".' not in inner_where               # no column of the outer table
    assert 'omop_languages_skills_required' in inner_where
    assert list(outer.values_list('id', flat=True)) == []   # en asked, not held: excluded
    trial.omop_languages_skills_required = []
    trial.save(update_fields=['omop_languages_skills_required'])
    assert list(outer.values_list('id', flat=True)) == [trial.id]


# ── non-string elements and an empty asked list ───────────────────────

def test_a_null_trial_element_is_no_requirement_in_python_and_sql():
    from exact_matching.omop.languages_match_profile import omop_languages_enabled
    trial = TrialFactory(disease='multiple myeloma', languages_skills_required=[],
                         omop_languages_skills_required=[None])
    patient = _patient(english_speak=False)                 # answered, holds nothing
    assert omop_languages_enabled()                          # a null passes the readiness gate
    assert _status(trial, patient) == 'matched'
    assert _kept(trial, patient)
    assert not _lang_potential(trial, patient)


@pytest.mark.parametrize('column', [[7], [None, 7], [{'x': 1}]])
def test_other_non_strings_are_no_requirement_and_fail_readiness(column):
    # The drift check refuses these (their text is no producible pair), so on a
    # real catalog they never reach the OMOP path; the verdicts still agree.
    from exact_matching.omop.languages_verdict import language_verdict, language_verdict_sql
    from django.db import connection
    assert language_verdict(column, [], [EN]) == 'matched'
    trial = TrialFactory(disease='multiple myeloma', languages_skills_required=[],
                         omop_languages_skills_required=column)
    required, _, _ = language_verdict_sql('omop_languages_skills_required', [], [EN])
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {required} FROM trials_trial WHERE id = %s', [trial.id])
        assert cursor.fetchone()[0] is False
    from trials.services.omop.languages_readiness import check_languages_readiness
    assert not check_languages_readiness().ok


def test_empty_inline_asked_list_with_pairs_is_never_not_matched():
    trial = TrialFactory(disease='multiple myeloma', languages_skills_required=['write__en'],
                         omop_languages_skills_required=[f'{EN}:2100007855'])
    patient = PatientInfo(disease='multiple myeloma', patient_age=60,
                          language_skill_concept_ids=[EN_SPEAK], language_asked_concept_ids=[])
    assert _status(trial, patient) == 'unknown'
    assert _kept(trial, patient)
    assert se.compare(Trial.objects.filter(id=trial.id), patient) == []


@pytest.mark.parametrize('value', ['\u0664\u0661\u0668\u0660\u0661\u0668\u0666'])
def test_non_ascii_digits_are_not_concept_ids(value):
    from exact_matching.omop.languages_verdict import _CID
    assert not _CID.fullmatch(value)


def test_backend_flag_off_does_not_touch_the_patient(settings):
    from exact_matching.backend import ExactMatcher
    settings.EXACT_OMOP_LANGUAGES = False
    trial = _trial('speak__en')

    class _NoPatient:
        def as_patient_info(self):
            raise AssertionError('as_patient_info called with the flag off')
    ExactMatcher().attrs_to_fill_in(trial, _NoPatient(), {'languages_skills': 1})
