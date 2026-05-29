from django.core.management.base import BaseCommand

from trials.models import Disease, TrialType, TrialTypeDiseaseConnection
from trials.services.loaders.load_bc_options import LoadBcOptions
from trials.services.loaders.load_concomitant_medications import LoadConcomitantMedications
from trials.services.loaders.load_ethnicity_options import LoadEthnicityOptions
from trials.services.loaders.load_genetic_mutations import LoadGeneticMutations
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.loaders.load_markers import LoadMarkers
from trials.services.loaders.load_mcl_options import LoadMclOptions
from trials.services.loaders.load_planned_therapy_options import LoadPlannedTherapyOptions
from trials.services.loaders.load_preferred_countries_options import LoadPreferredCountriesOptions
from trials.services.loaders.load_scth_options import LoadScthOptions
from trials.services.loaders.load_supportive_therapies import LoadSupportiveTherapies
from trials.services.loaders.load_tnm_options import LoadTnmOptions
from trials.services.loaders.load_toxicity_grade_options import LoadToxicityGradeOptions


BC_TRIAL_TYPES = [
    ('hormone_receptor_positive_her2_negative', 'Hormone Receptor-Positive / HER2-Negative (HR+/HER2-)'),
    ('her2_positive', 'HER2-Positive (HER2+)'),
    ('triple_negative', 'Triple-Negative Breast Cancer (TNBC)'),
    ('hormone_receptor_positive_her2_positive', 'Hormone Receptor-Positive / HER2-Positive (HR+/HER2+)'),
    ('neoadjuvant', 'Neoadjuvant'),
    ('adjuvant', 'Adjuvant'),
    ('metastatic', 'Metastatic'),
    ('early_stage', 'Early-Stage'),
    ('locally_advanced', 'Locally Advanced'),
    ('prevention_and_risk_reduction', 'Prevention & Risk Reduction'),
    ('survivorship_and_quality_of_life', 'Survivorship & Quality of Life'),
]

MM_TRIAL_TYPES = [
    ('antibody_based_immunotherapies', 'Antibody-Based Immunotherapies'),
    ('cellular_therapies', 'Cellular Therapies'),
    ('targeted_therapies', 'Targeted Therapies'),
    ('chemo_and_steroids', 'Chemo & Steroids'),
    ('transplants', 'Transplants'),
    ('radiotherapy', 'Radiotherapy'),
    ('vaccines', 'Vaccines'),
    ('maintenance', 'Maintenance'),
    ('supportive', 'Supportive'),
]

FL_TRIAL_TYPES = [
    ('antibody_based_immunotherapies', 'Antibody-Based Immunotherapies'),
    ('cellular_therapy', 'Cellular Therapy'),
    ('small_molecule_targeted_drugs', 'Small Molecule/Targeted Drugs'),
    ('chemotherapy_and_chemo_backbone_regimens', 'Chemotherapy and Chemo-Backbone Regimens'),
    ('radiotherapy_focused_trials', 'Radiotherapy-Focused Trials'),
    ('supportive', 'Supportive'),
]

CLL_TRIAL_TYPES = [
    ('antibody_based_immunotherapies', 'Antibody-Based Immunotherapies'),
    ('cellular_therapies', 'Cellular Therapies'),
    ('small_molecule_targeted_therapies', 'Small Molecule / Targeted Therapies'),
    ('chemotherapy_and_chemo_backbone_regimens', 'Chemotherapy & Chemo-Backbone Regimens'),
    ('chemo_and_steroids', 'Chemo & Steroids'),
    ('transplants', 'Transplants'),
    ('radiotherapy_focused_trials', 'Radiotherapy-Focused Trials'),
    ('vaccines', 'Vaccines'),
    ('maintenance', 'Maintenance'),
    ('supportive', 'Supportive'),
    ('targeted_therapy_btk_inhibitors', 'Targeted Therapy: BTK Inhibitors'),
    ('targeted_therapy_bcl_2_inhibitors', 'Targeted Therapy: BCL-2 Inhibitors'),
    ('targeted_therapy_pi3k_inhibitors', 'Targeted Therapy: PI3K Inhibitors'),
    ('mrd_guided_and_fixed_duration_strategies', 'MRD-Guided & Fixed-Duration Strategies'),
    ('infection_prevention_and_immune_reconstitution', 'Infection Prevention & Immune Reconstitution'),
]

PRE_EXISTING_CATEGORIES = [
    ('cardiacIssues', 'Cardiac Issues'),
    ('pulmonaryDisease', 'Pulmonary Disease'),
    ('renalImpairment', 'Renal Impairment'),
    ('hepaticImpairment', 'Hepatic Impairment'),
    ('infections', 'Infections'),
    ('otherActiveMalignancies', 'Other Active Malignancies'),
    ('neurologicalAndPsychiatricConditions', 'Neurological and Psychiatric Conditions'),
    ('autoimmuneAndInflammatoryDisorders', 'Autoimmune and Inflammatory Disorders'),
    ('pregnancyOrBreastfeeding', 'Pregnancy or Breastfeeding'),
    ('performanceStatus', 'Performance Status'),
    ('priorTherapies', 'Prior Therapies'),
]


class Command(BaseCommand):
    help = 'Seed all static reference data (ValueOptions, TrialTypes, PreExistingConditionCategories, etc.)'

    def handle(self, *args, **options):
        self.stdout.write('Seeding diseases...')
        self._seed_diseases()

        self.stdout.write('Seeding BC options (ER/PR/HER2/HRD/HR status, histologic types)...')
        LoadBcOptions().load_all(skip_hrd=False, skip_hr=False, skip_histologic_types=False)

        self.stdout.write('Seeding ethnicity options...')
        LoadEthnicityOptions().load_all()

        self.stdout.write('Seeding stem cell transplant history options...')
        LoadScthOptions().load_all()

        self.stdout.write('Seeding preferred countries...')
        LoadPreferredCountriesOptions().load_all()

        self.stdout.write('Seeding language options...')
        LoadLangOptions().load_all()

        self.stdout.write('Seeding toxicity grade options...')
        LoadToxicityGradeOptions().load_all()

        self.stdout.write('Seeding TNM staging options...')
        LoadTnmOptions().load_all()

        self.stdout.write('Seeding planned therapy options...')
        LoadPlannedTherapyOptions().load_all(skip_diseases=False)

        self.stdout.write('Seeding genetic mutations...')
        LoadGeneticMutations().load_all(skip_genes_origins=False)

        self.stdout.write('Seeding markers...')
        LoadMarkers().load_all()

        self.stdout.write('Seeding MCL options (protein expressions, morphologic variants)...')
        LoadMclOptions().load_all()

        self.stdout.write('Seeding supportive therapies...')
        LoadSupportiveTherapies().load_all()

        self.stdout.write('Seeding concomitant medications...')
        LoadConcomitantMedications().load_all()

        self.stdout.write('Seeding trial types...')
        self._seed_trial_types()

        self.stdout.write('Seeding pre-existing condition categories...')
        self._seed_pre_existing_categories()

        self.stdout.write(self.style.SUCCESS('Done.'))

    def _seed_diseases(self):
        from trials.models import Disease
        diseases = {
            'BC': 'Breast Cancer',
            'MM': 'Multiple Myeloma',
            'FL': 'Follicular Lymphoma',
            'CLL': 'Chronic Lymphocytic Leukemia',
            'MCL': 'Mantle Cell Lymphoma',
        }
        for code, title in diseases.items():
            Disease.objects.update_or_create(code=code, defaults={'title': title})

    def _seed_trial_types(self):
        disease_map = {
            'BC': BC_TRIAL_TYPES,
            'MM': MM_TRIAL_TYPES,
            'FL': FL_TRIAL_TYPES,
            'CLL': CLL_TRIAL_TYPES,
        }
        for disease_code, types in disease_map.items():
            disease = Disease.objects.filter(code=disease_code).first()
            if not disease:
                self.stdout.write(self.style.WARNING(f'  Disease {disease_code} not found, skipping trial types'))
                continue
            for code, title in types:
                trial_type, _ = TrialType.objects.get_or_create(code=code, defaults={'title': title})
                TrialTypeDiseaseConnection.objects.get_or_create(trial_type=trial_type, disease=disease)

    def _seed_pre_existing_categories(self):
        from trials.models import PreExistingConditionCategory
        for code, title in PRE_EXISTING_CATEGORIES:
            PreExistingConditionCategory.objects.update_or_create(code=code, defaults={'title': title})
