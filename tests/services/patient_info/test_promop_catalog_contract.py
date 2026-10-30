"""#658: real adapter/resolver paths must not read unrelated retired catalogs."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from trials.services.patient_info.promop_adapter import (
    _build_code_lookup,
    build_patient_info_from_promop_row,
    normalize_promop_row,
    UnsupportedPatientVocabulary,
)
from trials.services.patient_info.resolve import resolve_patient_info
from trials.services.patient_vocabulary import PatientVocabularyUnavailable


@pytest.fixture(autouse=True)
def fresh_catalog_cache():
    _build_code_lookup.cache_clear()
    yield
    _build_code_lookup.cache_clear()


def _no_retired_catalogs(execute, sql, params, many, context):
    assert '"trials_marker"' not in sql
    assert '"trials_markercategory"' not in sql
    return execute(sql, params, many, context)


@pytest.mark.django_db
@pytest.mark.parametrize('field,value,expected', [
    ('hr_status', 'Positive', 'hr_plus'),
    ('her2_status', 'Equivocal', None),
    ('her2_status', 'HER2 low', 'her2_low'),
    ('her2_status', 'Negative', 'her2_minus'),
    ('estrogen_receptor_status', 'Positive', 'er_plus'),
    ('progesterone_receptor_status', 'Positive', 'pr_plus'),
    ('estrogen_receptor_status', 'er_plus_with_hi_exp', 'er_plus_with_hi_exp'),
    ('estrogen_receptor_status', 'ER+ with high expression', 'er_plus_with_hi_exp'),
    ('estrogen_receptor_status', 'Borderline', None),
    ('progesterone_receptor_status', 'Negative', 'pr_minus'),
    ('her2_status', 'Unknown', None),
    ('ethnicity', 'White', 'caucasian_or_european'),
])
def test_normalizer_does_not_query_retired_or_unrelated_catalogs(field, value, expected):
    def only_relevant_catalog(execute, sql, params, many, context):
        _no_retired_catalogs(lambda *args: None, sql, params, many, context)
        assert '"trials_therapy"' not in sql
        assert '"trials_mutationgene"' not in sql
        return execute(sql, params, many, context)

    with connection.execute_wrapper(only_relevant_catalog):
        row = normalize_promop_row({field: value})
        assert row[field] == expected
        assert normalize_promop_row(dict(row))[field] == expected


@pytest.mark.django_db
@override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
def test_real_promop_resolver_uses_correct_receptor_semantics():
    request = SimpleNamespace(data={}, query_params={'person_id': '9001'})
    row = {
        'disease': 'breast cancer', 'patient_age': 50,
        'estrogen_receptor_status': 'Positive',
        'progesterone_receptor_status': 'Negative', 'her2_status': 'Equivocal',
    }
    with patch('trials.services.patient_info.promop_client.PromopClient.fetch_patient',
               return_value=row), connection.execute_wrapper(_no_retired_catalogs):
        patient = resolve_patient_info(request)
    assert patient.estrogen_receptor_status == 'er_plus'
    assert patient.progesterone_receptor_status == 'pr_minus'
    assert patient.her2_status is None
    assert patient.hr_status == 'hr_plus'


@pytest.mark.django_db
def test_builder_preserves_explicit_negative_receptors():
    with connection.execute_wrapper(_no_retired_catalogs):
        patient = build_patient_info_from_promop_row({
            'disease': 'breast cancer', 'estrogen_receptor_status': 'Negative',
            'progesterone_receptor_status': 'Negative', 'her2_status': 'Negative',
        })
    assert patient.tnbc_status is True
    assert patient.hr_status == 'hr_minus'


@pytest.mark.django_db
@pytest.mark.parametrize('field,value,expected', [
    ('cytogenetic_markers', 'del17p', 'del17p13'),
    ('cytogenic_markers', 'Del(17p13) Deletion,t(4;14) Translocation', 'del17p13,t414'),
    ('cytogenetic_markers', '1q_gain, 1q_amp', '1q21Amplification'),
    ('cytogenetic_markers', 'del17p, del17p', 'del17p13'),
    ('cytogenetic_markers', 'del17p13,t414', 'del17p13,t414'),
    ('molecular_markers', 'TP53 Mutation', 'tp53Mutation'),
    ('molecular_markers', 'tp53Mutation', 'tp53Mutation'),
    ('molecular_markers', '', ''),
    ('molecular_markers', None, ''),
    ('cytogenetic_markers', 'Unknown', ''),
    ('cytogenetic_markers', 'none', 'none'),
    ('molecular_markers', 'none', 'none'),
])
def test_marker_axes_resolve_every_token_and_roundtrip(field, value, expected):
    target = 'cytogenic_markers' if field != 'molecular_markers' else field
    with connection.execute_wrapper(_no_retired_catalogs):
        row = normalize_promop_row({field: value})
        assert row[target] == expected
        assert normalize_promop_row(dict(row))[target] == expected


@pytest.mark.django_db
@pytest.mark.parametrize('field,value', [
    ('cytogenetic_markers', 'del13q, del17p'),
    ('cytogenetic_markers', 'del13q'),
    ('cytogenetic_markers', 'tp53Mutation'),  # wrong axis
    ('cytogenetic_markers', 'none, del17p'),
    ('cytogenetic_markers', 'Unknown, del17p'),
    ('cytogenetic_markers', 'del17p,'),
    ('molecular_markers', 'TP53 Mutation, unsupported-private-token'),
    ('molecular_markers', 'Complex Karyotype (unreviewed qualifier)'),
    ('molecular_markers', ['tp53Mutation']),
])
def test_incomplete_or_contradictory_marker_sets_are_refused(field, value):
    with pytest.raises(UnsupportedPatientVocabulary) as error:
        build_patient_info_from_promop_row({'disease': 'multiple myeloma', field: value})
    assert error.value.status_code == 422
    assert 'unsupported-private-token' not in str(error.value.detail)


@pytest.mark.django_db
def test_options_use_current_axes_and_do_not_depend_on_myeloma_table():
    from importlib import import_module
    from exact_matching.patient_info.patient_info_attributes import MYELOMA_RELATED_DISORDERS_SOURCES
    from trials.services.value_options import ValueOptions

    def no_obsolete_references(execute, sql, params, many, context):
        _no_retired_catalogs(lambda *args: None, sql, params, many, context)
        assert '"trials_myelomarelateddisorder"' not in sql
        return execute(sql, params, many, context)

    with connection.execute_wrapper(no_obsolete_references):
        options = ValueOptions()
        assert options.cytogenic_markers['del17p13'] == 'Del(17p13) Deletion'
        assert options.molecular_markers['tp53Mutation'] == 'TP53 Mutation'
        labels = options.myeloma_related_disorders
        assert set(labels) == set(MYELOMA_RELATED_DISORDERS_SOURCES)
        seed = import_module('trials.migrations.0026_myeloma_related_disorders_seed')
        assert labels == {code: title for code, title, _ in seed.MYELOMA_RELATED_DISORDERS}


@pytest.mark.django_db
def test_empty_catalog_is_explicit_capability_failure_and_is_not_cached():
    from trials.models import CytogenicMarker
    from trials.services.value_options import ValueOptions

    CytogenicMarker.objects.all().delete()
    with pytest.raises(PatientVocabularyUnavailable):
        normalize_promop_row({'cytogenetic_markers': 'del17p'})
    with pytest.raises(PatientVocabularyUnavailable):
        _ = ValueOptions().cytogenic_markers
    # Unrelated receptors still work, even though the marker catalog is absent.
    assert normalize_promop_row({'hr_status': 'Positive'})['hr_status'] == 'hr_plus'
    CytogenicMarker.objects.create(code='del17p13', title='Del(17p13) Deletion')
    assert normalize_promop_row({'cytogenetic_markers': 'del17p'})['cytogenic_markers'] == 'del17p13'


@pytest.mark.django_db
def test_normalizer_http_refuses_partial_markers_without_leaking_values():
    client = APIClient()
    client.force_authenticate(user=SimpleNamespace(is_authenticated=True, pk=9001))
    response = client.post('/normalize-promop-row/', {
        'molecular_markers': 'TP53 Mutation, unsupported-private-token',
    }, format='json')
    assert response.status_code == 422
    assert response.json()['code'] == 'unsupported_patient_vocabulary'
    assert 'unsupported-private-token' not in response.content.decode()


@pytest.mark.django_db
def test_missing_catalog_table_is_503_but_does_not_affect_receptor_only_input():
    def missing_catalog(execute, sql, params, many, context):
        return execute(sql.replace('"trials_cytogenicmarker"', '"missing_658_catalog"'),
                       params, many, context)

    with connection.execute_wrapper(missing_catalog):
        with pytest.raises(PatientVocabularyUnavailable), transaction.atomic():
            normalize_promop_row({'cytogenetic_markers': 'del17p'})
        assert normalize_promop_row({'hr_status': 'Positive'})['hr_status'] == 'hr_plus'


@pytest.mark.django_db
@override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
def test_search_and_details_refuse_unsupported_marker_input():
    from trials.models import Trial

    trial = Trial.objects.create(disease='multiple myeloma')
    client = APIClient()
    client.force_authenticate(user=SimpleNamespace(is_authenticated=True, pk=9002))
    for url in ('/trials/search/?person_id=9001', f'/trials/{trial.pk}/?person_id=9001'):
        with patch('trials.services.patient_info.promop_client.PromopClient.fetch_patient',
                   return_value={'disease': 'multiple myeloma', 'cytogenetic_markers': 'del13q, del17p'}):
            response = client.get(url)
        assert response.status_code == 422
        assert response.json()['code'] == 'unsupported_patient_vocabulary'
        assert 'del13q' not in response.content.decode()


@pytest.mark.django_db
def test_standalone_migration_preserves_legacy_marker_rows():
    from importlib import import_module
    from django.apps import apps
    from trials.models import CytogenicMarker, Marker, MarkerCategory, MolecularMarker

    marker = Marker.objects.create(code='fixture658', title='Fixture 658', description='preserved')
    category, _ = MarkerCategory.objects.get_or_create(code='cytogenic', defaults={'title': 'Cytogenic Markers'})
    marker.categories.add(category)
    migration = import_module('trials.migrations.0027_cb_marker_catalogs')
    editor = SimpleNamespace(connection=connection)
    migration.copy_standalone_markers(apps, editor)
    migration.copy_standalone_markers(apps, editor)
    assert CytogenicMarker.objects.filter(code=marker.code).count() == 1
    copied = CytogenicMarker.objects.get(code=marker.code)
    assert (copied.title, copied.description) == (marker.title, marker.description)
    assert not MolecularMarker.objects.filter(code=marker.code).exists()
    assert Marker.objects.get(pk=marker.pk).description == 'preserved'


def test_standalone_seed_and_copy_refuse_external_catalog():
    from importlib import import_module
    from django.core.exceptions import ImproperlyConfigured
    from trials.services.loaders.load_markers import LoadMarkers

    with patch('django.conf.settings.DATABASES', {'default': {}, 'trials': {}}):
        with pytest.raises(ImproperlyConfigured, match='standalone-only'):
            LoadMarkers().load_all()
        # Must return before touching even an apps registry or schema editor.
        migration = import_module('trials.migrations.0027_cb_marker_catalogs')
        migration.copy_standalone_markers(None, None)
