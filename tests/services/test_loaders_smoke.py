"""Smoke + idempotency tests for the four loaders not exercised by conftest (#30).

`tests/conftest.py` runs 11 of the 15 loaders at session setup (so they
get implicit coverage — a regression there breaks the whole test suite
at fixture load). The four exceptions — `LoadLangOptions`,
`LoadPreferredCountriesOptions`, `LoadScthOptions`, `LoadTnmOptions`
— have **no** test exercise at all and are only invoked via the
`seed_reference_data` management command in production.

These tests close the gap by asserting each `load_all()`:
1. Runs without raising.
2. Creates at least one row in each model it targets.
3. Is idempotent — re-running keeps the row count stable.
"""
import pytest

from trials.models import (
    DistantMetastasisStage,
    Language,
    LanguageSkillLevel,
    NodesStage,
    PreferredCountry,
    StagingModality,
    StemCellTransplant,
    TumorStage,
)
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.loaders.load_preferred_countries_options import LoadPreferredCountriesOptions
from trials.services.loaders.load_scth_options import LoadScthOptions
from trials.services.loaders.load_tnm_options import LoadTnmOptions


# Each entry: (loader_class, [(Model, expected_min_count), ...])
_LOADERS_UNDER_TEST = [
    (LoadLangOptions, [(Language, 1), (LanguageSkillLevel, 1)]),
    (LoadPreferredCountriesOptions, [(PreferredCountry, 1)]),
    (LoadScthOptions, [(StemCellTransplant, 1)]),
    (LoadTnmOptions, [
        (TumorStage, 1),
        (NodesStage, 1),
        (DistantMetastasisStage, 1),
        (StagingModality, 1),
    ]),
]


@pytest.mark.django_db
@pytest.mark.parametrize('loader_cls,model_expectations', _LOADERS_UNDER_TEST)
def test_load_all_runs_and_populates(loader_cls, model_expectations):
    loader_cls().load_all()
    for model, min_count in model_expectations:
        assert model.objects.count() >= min_count, (
            f'{loader_cls.__name__} did not populate {model.__name__} '
            f'(expected ≥ {min_count})'
        )


@pytest.mark.django_db
@pytest.mark.parametrize('loader_cls,model_expectations', _LOADERS_UNDER_TEST)
def test_load_all_is_idempotent(loader_cls, model_expectations):
    """Re-running `load_all()` must not duplicate rows. Each loader uses
    `update_or_create` keyed on `code`, so a second run should leave the
    count unchanged — silent duplication would corrupt downstream
    eligibility lookups that join on these reference tables.
    """
    loader_cls().load_all()
    counts_after_first_run = {
        model: model.objects.count() for model, _ in model_expectations
    }

    loader_cls().load_all()
    for model, _ in model_expectations:
        assert model.objects.count() == counts_after_first_run[model], (
            f'{loader_cls.__name__} created duplicates in '
            f'{model.__name__} on re-run '
            f'({counts_after_first_run[model]} → {model.objects.count()})'
        )
