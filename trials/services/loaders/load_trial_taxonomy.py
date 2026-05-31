"""Idempotent loader for TrialType rows and TrialTypeDiseaseConnection records.

The data migration `0005_seed_trial_taxonomy` creates TrialType rows at
migrate time, but it cannot create TrialTypeDiseaseConnection records
then because Disease rows are seeded later by domain loaders
(`LoadConcomitantMedications`, `LoadPlannedTherapyOptions`,
`LoadMclOptions`, …). This loader closes that gap: it re-seeds
TrialType (taxonomy is authoritative for titles) and creates connections
after Disease rows exist.

Run order in `tests/conftest.py` and `seed_reference_data` is: all
Disease-seeding loaders first, then this loader.

Disease lookup is case-insensitive because EXACT mixes lowercase
('mm', 'fl', 'bc', 'cll') and uppercase ('MCL') Disease.code values
while the taxonomy uses uppercase throughout.
"""
from trials.models import Disease, TrialType, TrialTypeDiseaseConnection
from trials.trial_taxonomy import ALL_TRIAL_TYPES


class LoadTrialTaxonomy:
    def load_all(self):
        self.load_trial_types()
        self.load_connections()

    def load_trial_types(self):
        """Taxonomy is authoritative for titles — refresh on every run."""
        for code, title, _diseases in ALL_TRIAL_TYPES:
            TrialType.objects.update_or_create(
                code=code, defaults={'title': title}
            )

    def load_connections(self):
        for code, _title, diseases in ALL_TRIAL_TYPES:
            try:
                trial_type = TrialType.objects.get(code=code)
            except TrialType.DoesNotExist:
                continue
            for disease_code in diseases:
                # Iterate over every Disease row matching the code
                # case-insensitively. EXACT has duplicate Disease rows
                # for MM/FL/BC (lowercase from `load_planned_therapy_options`,
                # uppercase from `load_concomitant_medications`); linking
                # only one would leave queries against the other row
                # silently disease-blind. Mirrors the multi-row pattern
                # used in `services/value_options.py`.
                for disease in Disease.objects.filter(code__iexact=disease_code):
                    TrialTypeDiseaseConnection.objects.get_or_create(
                        trial_type=trial_type, disease=disease
                    )
