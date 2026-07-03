"""OMOP cutover phase 3a: vocab loader + trial backfill + shadow-compare.

Ported machinery (CB epic #4447): map legacy therapy codes -> OMOP concept_id
strings via the vocab models' omop_concept_id, populate the trial omop_* columns,
and report drift / cutover-divergence. Nothing reads the omop columns for matching
yet (that is gated behind the feature flag in a later phase).
"""
import csv
from io import StringIO

import pytest
from django.core.management import call_command

from trials.models import (
    Therapy, TherapyComponent, TherapyComponentCategory, OmopConcept, TherapyOmopMapping,
)
from trials.services.omop.therapy_concept_mapper import (
    build_omop_columns,
    map_codes_to_concept_ids,
)
from trials.services.omop.therapy_sync import sync_trial_omop_columns
from trials.services.omop.shadow_compare import compare_corpus, compare_trial
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def test_map_codes_to_concept_ids_strings_dedup_sorted_and_unmapped():
    # Fake zz_* codes so they never collide with the session-seeded reference data.
    Therapy.objects.create(code='zz_td', title='zz Td', omop_concept_id=222)
    Therapy.objects.create(code='zz_vrd', title='zz VRd', omop_concept_id=111)
    Therapy.objects.create(code='zz_no_map', title='zz Unmapped', omop_concept_id=None)
    concept_ids, unmapped = map_codes_to_concept_ids(
        Therapy, ['zz_vrd', 'zz_td', 'zz_vrd', 'zz_no_map', 'zz_unknown_code']
    )
    # stringified, de-duplicated, sorted
    assert concept_ids == ['111', '222']
    # null omop_concept_id and unknown code both reported as unmapped, sorted
    assert unmapped == ['zz_no_map', 'zz_unknown_code']


def test_map_codes_to_concept_ids_empty():
    assert map_codes_to_concept_ids(Therapy, []) == ([], [])


def test_build_omop_columns_all_six_levels():
    Therapy.objects.create(code='reg_r', title='reg r', omop_concept_id=1)
    Therapy.objects.create(code='reg_x', title='reg x', omop_concept_id=2)
    TherapyComponent.objects.create(code='comp_r', title='comp r', omop_concept_id=3)
    TherapyComponentCategory.objects.create(code='cat_r', title='cat r', omop_concept_id=4)
    trial = TrialFactory(
        therapies_required=['reg_r'],
        therapies_excluded=['reg_x'],
        therapy_components_required=['comp_r'],
        therapy_types_required=['cat_r'],
    )
    values, unmapped = build_omop_columns(trial)
    assert values['omop_therapies_required'] == ['1']
    assert values['omop_therapies_excluded'] == ['2']
    assert values['omop_therapy_components_required'] == ['3']
    assert values['omop_therapy_types_required'] == ['4']
    # untouched legacy columns map to empty
    assert values['omop_therapy_components_excluded'] == []
    assert unmapped == {}


def test_sync_trial_omop_columns_persists_and_is_idempotent():
    Therapy.objects.create(code='reg_r', title='reg r', omop_concept_id=1)
    trial = TrialFactory(therapies_required=['reg_r'])
    values, unmapped, changed = sync_trial_omop_columns(trial.id)
    assert changed is True
    trial.refresh_from_db()
    assert trial.omop_therapies_required == ['1']
    # second run: nothing to do
    _, _, changed2 = sync_trial_omop_columns(trial.id)
    assert changed2 is False


def test_sync_trial_omop_columns_missing_trial():
    assert sync_trial_omop_columns(999_999) == (None, None, False)


def test_shadow_compare_reports_unmapped_divergence():
    Therapy.objects.create(code='mapped', title='mapped', omop_concept_id=1)
    Therapy.objects.create(code='nomap', title='nomap', omop_concept_id=None)
    trial = TrialFactory(therapies_required=['mapped', 'nomap'])
    sync_trial_omop_columns(trial.id)
    trial.refresh_from_db()
    drift, unmapped = compare_trial(trial)
    assert drift == {}  # stored matches the mapping (we just synced)
    assert unmapped == {'therapies_required': ['nomap']}


def test_shadow_compare_detects_drift_when_not_synced():
    Therapy.objects.create(code='mapped', title='mapped', omop_concept_id=1)
    trial = TrialFactory(therapies_required=['mapped'])  # omop columns left empty
    report = compare_corpus(trial.__class__.objects.filter(pk=trial.pk))
    assert report['drifted_trials'] == 1
    assert 'omop_therapies_required' in report['drift_by_col']


def test_load_therapy_omop_concept_ids_command(tmp_path):
    # Fake zz_* codes so the test owns the rows (no collision with seeded vocab).
    Therapy.objects.create(code='zz_thalidomide', title='zz Thalidomide', omop_concept_id=None)
    TherapyComponent.objects.create(code='zz_lipodox', title='zz Liposomal Doxorubicin')
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_thalidomide', 'zz Thalidomide', '19137042', 'thalidomide', 'RxNorm', 'auto'])
        w.writerow(['component', 'zz_lipodox', 'zz Liposomal Doxorubicin', '1338512', 'doxorubicin', 'RxNorm', 'llm'])
        w.writerow(['regimen', 'zz_skip_me', 'Skip', '', '', '', 'needs_review'])
    out = StringIO()
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), stdout=out)
    assert Therapy.objects.get(code='zz_thalidomide').omop_concept_id == 19137042
    assert TherapyComponent.objects.get(code='zz_lipodox').omop_concept_id == 1338512
    # OmopConcept titles upserted (concept_id -> name/vocab) for resolving names
    assert OmopConcept.objects.get(concept_id=19137042).concept_name == 'thalidomide'
    assert OmopConcept.objects.get(concept_id=19137042).vocabulary_id == 'RxNorm'
    assert OmopConcept.objects.get(concept_id=1338512).concept_name == 'doxorubicin'


def test_load_populates_omop_concept_without_vocab_row(tmp_path):
    # OmopConcept is keyed by concept_id → populated even when cb_code isn't a vocab
    # row; empty omop_vocab → None; rows without a concept are not created.
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_not_vocab', 'X', '870101', 'somedrug', 'RxNorm', 'curated'])
        w.writerow(['component', 'zz_novocab', 'Y', '870102', 'otherdrug', '', 'auto'])
        w.writerow(['regimen', 'zz_skip', 'Z', '', '', '', 'needs_review'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), stdout=StringIO())
    assert OmopConcept.objects.get(concept_id=870101).concept_name == 'somedrug'
    assert OmopConcept.objects.get(concept_id=870102).vocabulary_id is None  # empty vocab
    assert not OmopConcept.objects.filter(concept_name='').exists()


def test_load_dry_run_skips_omop_concept(tmp_path):
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_x', 'X', '870199', 'thalidomide', 'RxNorm', 'curated'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), dry_run=True, stdout=StringIO())
    assert not OmopConcept.objects.filter(concept_id=870199).exists()


def test_load_therapy_omop_concept_ids_exclude_llm(tmp_path):
    TherapyComponent.objects.create(code='zz_lipodox2', title='zz Liposomal Doxorubicin 2')
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['component', 'zz_lipodox2', 'zz Liposomal Doxorubicin 2', '1338512', 'doxorubicin', 'RxNorm', 'llm'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), include_llm=False, stdout=StringIO())
    # llm row skipped when --exclude-llm
    assert TherapyComponent.objects.get(code='zz_lipodox2').omop_concept_id is None


def test_load_populates_crosswalk_including_unmapped(tmp_path):
    # The crosswalk (#4476) records EVERY CSV row — mapped and unmapped — keyed
    # by (level, cb_code), so coverage / SME gaps live in the DB, not just the file.
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_vrd', 'ZZ VRd', '111', 'RVD', 'RxNorm', 'auto'])
        w.writerow(['component', 'zz_bort', 'ZZ Bort', '222', 'bortezomib', 'RxNorm', 'curated'])
        w.writerow(['regimen', 'zz_asct', 'ZZ ASCT', '', '', '', 'no_omop'])
        w.writerow(['regimen', 'zz_epd', 'ZZ EPd', '', '', '', 'needs_review'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), stdout=StringIO())
    assert TherapyOmopMapping.objects.count() == 4
    vrd = TherapyOmopMapping.objects.get(level='regimen', cb_code='zz_vrd')
    assert vrd.omop_concept_id == 111 and vrd.omop_name == 'RVD' and vrd.match == 'auto'
    asct = TherapyOmopMapping.objects.get(level='regimen', cb_code='zz_asct')
    assert asct.omop_concept_id is None and asct.match == 'no_omop'
    unmapped = set(
        TherapyOmopMapping.objects.filter(omop_concept_id__isnull=True).values_list('cb_code', flat=True)
    )
    assert unmapped == {'zz_asct', 'zz_epd'}


def test_crosswalk_records_llm_rows_even_when_excluded_from_vocab(tmp_path):
    # --exclude-llm gates the vocab/OmopConcept writes, but the crosswalk still
    # records the llm row (it documents the mapping regardless of load policy).
    TherapyComponent.objects.create(code='zz_ide', title='ZZ Ide')
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_ide', 'ZZ Ide', '333', 'idecabtagene', 'RxNorm', 'llm'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), include_llm=False, stdout=StringIO())
    row = TherapyOmopMapping.objects.get(level='regimen', cb_code='zz_ide')
    assert row.omop_concept_id == 333 and row.match == 'llm'


def test_crosswalk_is_idempotent_and_updates_in_place(tmp_path):
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_vrd', 'ZZ VRd', '', '', '', 'needs_review'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), stdout=StringIO())
    row = TherapyOmopMapping.objects.get(level='regimen', cb_code='zz_vrd')
    assert row.omop_concept_id is None and row.match == 'needs_review'
    # re-load the same (level, cb_code) now mapped → updates in place, no dup
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_vrd', 'ZZ VRd', '111', 'RVD', 'RxNorm', 'curated'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), stdout=StringIO())
    assert TherapyOmopMapping.objects.count() == 1
    row.refresh_from_db()
    assert row.omop_concept_id == 111 and row.match == 'curated'


def test_dry_run_does_not_write_crosswalk(tmp_path):
    csv_path = tmp_path / 'm.csv'
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['level', 'cb_code', 'cb_title', 'omop_concept_id', 'omop_name', 'omop_vocab', 'match'])
        w.writerow(['regimen', 'zz_vrd', 'ZZ VRd', '111', 'RVD', 'RxNorm', 'auto'])
    call_command('load_therapy_omop_concept_ids', csv=str(csv_path), dry_run=True, stdout=StringIO())
    assert TherapyOmopMapping.objects.count() == 0


def test_backfill_command_populates_trial_columns():
    Therapy.objects.create(code='reg_r', title='reg r', omop_concept_id=42)
    trial = TrialFactory(therapies_required=['reg_r'])
    call_command('backfill_omop_therapy_columns', stdout=StringIO())
    trial.refresh_from_db()
    assert trial.omop_therapies_required == ['42']


def test_vendored_mapping_csv_is_well_formed():
    """The CSV vendored from CB must keep the columns the loader reads."""
    from trials.management.commands.load_therapy_omop_concept_ids import DEFAULT_CSV
    with open(DEFAULT_CSV) as f:
        reader = csv.DictReader(f)
        assert set(reader.fieldnames) >= {'level', 'cb_code', 'omop_concept_id', 'match'}
        levels = {row['level'] for row in reader}
    assert levels <= {'regimen', 'component', 'category'}
