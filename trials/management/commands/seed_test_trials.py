"""
Management command: seed_test_trials

Creates a small set of fake trials for local end-to-end testing.
Covers all four supported diseases (MM, FL, BC, CLL) with enough
variation to produce both eligible and potential matches against
the test patients in the patient database.

Usage:
    python manage.py seed_reference_data   # run first
    python manage.py seed_test_trials
"""
from django.core.management.base import BaseCommand

from trials.models import Trial, TrialType


TRIALS = [
    # ── Multiple Myeloma ──────────────────────────────────────────────
    dict(
        code='TEST-MM-001',
        study_id='NCT-TEST-0001',
        brief_title='[TEST] Daratumumab + Pomalidomide + Dexamethasone in R/R MM',
        disease='multiple myeloma',
        register='ClinicalTrials.gov',
        sponsor_name='Test Pharma Inc.',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        # Patient requirements — intentionally broad to allow matches
        age_low_limit=18,
        age_high_limit=80,
        prior_therapy_lines='moreThanOne',
        therapy_lines_count_min=1,
        therapy_lines_count_max=4,
        therapies_required=[],
        therapies_excluded=[],
        no_hiv_required=True,
        no_hepatitis_b_required=True,
        no_hepatitis_c_required=True,
        # Scoring (0–20 scale; see Trial.get_goodness_score)
        benefit_score=16,
        patient_burden_score=6,
        risk_score=4,
    ),
    dict(
        code='TEST-MM-002',
        study_id='NCT-TEST-0002',
        brief_title='[TEST] CAR-T Therapy in Newly Diagnosed MM',
        disease='multiple myeloma',
        register='ClinicalTrials.gov',
        sponsor_name='Cellular Therapeutics Ltd.',
        recruitment_status='RECRUITING',
        phases=['Phase 2'],
        age_low_limit=18,
        age_high_limit=75,
        prior_therapy_lines='none',
        therapy_lines_count_min=0,
        therapy_lines_count_max=0,
        benefit_score=18,
        patient_burden_score=12,
        risk_score=8,
    ),

    # ── Follicular Lymphoma ───────────────────────────────────────────
    dict(
        code='TEST-FL-001',
        study_id='NCT-TEST-0003',
        brief_title='[TEST] Obinutuzumab + Bendamustine vs R-CHOP in Untreated FL',
        disease='follicular lymphoma',
        register='ClinicalTrials.gov',
        sponsor_name='Lymphoma Research Group',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        age_low_limit=18,
        age_high_limit=85,
        prior_therapy_lines='none',
        therapy_lines_count_min=0,
        therapy_lines_count_max=0,
        benefit_score=15,
        patient_burden_score=8,
        risk_score=5,
    ),
    dict(
        code='TEST-FL-002',
        study_id='NCT-TEST-0004',
        brief_title='[TEST] Mosunetuzumab in R/R Follicular Lymphoma',
        disease='follicular lymphoma',
        register='ClinicalTrials.gov',
        sponsor_name='Test Oncology Corp.',
        recruitment_status='RECRUITING',
        phases=['Phase 2'],
        age_low_limit=18,
        age_high_limit=85,
        prior_therapy_lines='moreThanOne',
        therapy_lines_count_min=2,
        benefit_score=14,
        patient_burden_score=5,
        risk_score=4,
    ),

    # ── Breast Cancer ─────────────────────────────────────────────────
    dict(
        code='TEST-BC-001',
        study_id='NCT-TEST-0005',
        brief_title='[TEST] Pembrolizumab + Chemotherapy in TNBC',
        disease='breast cancer',
        register='ClinicalTrials.gov',
        sponsor_name='Test Immuno Pharma',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        age_low_limit=18,
        age_high_limit=80,
        tnbc_status=True,
        benefit_score=17,
        patient_burden_score=10,
        risk_score=7,
    ),
    dict(
        code='TEST-BC-002',
        study_id='NCT-TEST-0006',
        brief_title='[TEST] Olaparib in HER2-Negative Advanced BC',
        disease='breast cancer',
        register='ClinicalTrials.gov',
        sponsor_name='AstraTest Ltd.',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        age_low_limit=18,
        age_high_limit=80,
        benefit_score=16,
        patient_burden_score=6,
        risk_score=4,
    ),

    # ── CLL ───────────────────────────────────────────────────────────
    dict(
        code='TEST-CLL-001',
        study_id='NCT-TEST-0007',
        brief_title='[TEST] Zanubrutinib vs Ibrutinib in R/R CLL',
        disease='chronic lymphocytic leukemia',
        register='ClinicalTrials.gov',
        sponsor_name='BeiTest Inc.',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        age_low_limit=18,
        age_high_limit=85,
        prior_therapy_lines='moreThanOne',
        therapy_lines_count_min=1,
        benefit_score=16,
        patient_burden_score=5,
        risk_score=4,
    ),
    dict(
        code='TEST-CLL-002',
        study_id='NCT-TEST-0008',
        brief_title='[TEST] Venetoclax + Obinutuzumab in Treatment-Naive CLL',
        disease='chronic lymphocytic leukemia',
        register='ClinicalTrials.gov',
        sponsor_name='AbbTest Oncology',
        recruitment_status='RECRUITING',
        phases=['Phase 3'],
        age_low_limit=18,
        age_high_limit=85,
        prior_therapy_lines='none',
        therapy_lines_count_min=0,
        therapy_lines_count_max=0,
        benefit_score=18,
        patient_burden_score=7,
        risk_score=4,
    ),
]


class Command(BaseCommand):
    help = 'Create fake test trials for local end-to-end testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all TEST-* trials before seeding.',
        )

    def handle(self, *args, **options):
        if options['clear']:
            deleted, _ = Trial.objects.filter(code__startswith='TEST-').delete()
            self.stdout.write(f'Deleted {deleted} existing test trials.')

        trial_types = {tt.code: tt for tt in TrialType.objects.all()}

        created = updated = 0
        for spec in TRIALS:
            code = spec.pop('code')
            # trial_type: match by disease prefix if available
            trial_type = None
            for tc, tt in trial_types.items():
                if tc.lower() in spec.get('disease', '').lower():
                    trial_type = tt
                    break

            obj, was_created = Trial.objects.update_or_create(
                code=code,
                defaults={
                    **spec,
                    'trial_type': trial_type,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Test trials: {created} created, {updated} updated  '
            f'(total TEST-* trials: {Trial.objects.filter(code__startswith="TEST-").count()})'
        ))
