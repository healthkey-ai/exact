from django.utils.functional import cached_property
from django.db.models import F

from django.core.cache import cache

from trials.models import *


def ordered_dict(data):
    data_keys = sorted(list(data.keys()))
    return {i: data[i] for i in data_keys}


class ValueOptions:
    def cache_key(self):
        # v2: bumped on #63 — `all_options()` gained 40 new per-disease
        # keys (flipiScore{Mm|Fl|Bc|Cll|Mcl}, cytogenicMarkers{…}, …).
        # Without the bump, a deploy hitting a populated Redis cache
        # would serve the pre-#63 shape (missing those 40 keys), letting
        # the cached blob drift from fresh `all_options()` output until
        # the 1h TTL expires.
        return 'ValueOptions.all_options.v2'

    @staticmethod
    def to_value_and_label(data):
        return [{'value': k, 'label': v} for k, v in data.items()]

    @cached_property
    def _disease_by_code_cache(self):
        # Per-instance memo so one all_options() build (which fans out to ~40
        # per-disease/line therapy lists) resolves each Disease once instead of
        # re-running `Disease WHERE code IN (lower, upper)` for every list — an
        # N+1 (ported from CB perf(value-options) memoize disease-by-code). A
        # fresh ValueOptions is built per all_options() call, so there is no
        # cross-request staleness.
        return {}

    def _disease_by_code(self, disease_code):
        from trials.models import Disease
        key = disease_code.upper()
        if key not in self._disease_by_code_cache:
            self._disease_by_code_cache[key] = list(
                Disease.objects.filter(code__in=[disease_code.lower(), disease_code.upper()])
            )
        return self._disease_by_code_cache[key]

    def therapies_by_disease_code_and_line_code(self, disease_code, line_code):
        from trials.models import Therapy, TherapyRound, DiseaseRoundTherapyConnection

        disease = self._disease_by_code(disease_code)
        therapy_line = TherapyRound.objects.filter(code=line_code).first()
        connections = DiseaseRoundTherapyConnection.objects.filter(disease__in=disease, round=therapy_line).select_related('therapy')
        items = [x.therapy.id for x in connections]
        items = Therapy.objects.filter(id__in=items).prefetch_related('components').order_by('id')
        options = {x.code: x.full_title() for x in items}
        return {
            '': 'Unknown/Other',
            **options
        }

    @staticmethod
    def therapies_all():
        from trials.models import Therapy

        items = Therapy.objects.all()
        return ordered_dict({x.code: x.title for x in items})

    @staticmethod
    def therapy_components_all():
        from trials.models import TherapyComponent

        items = TherapyComponent.objects.all()
        return ordered_dict({x.code: x.title for x in items})

    @staticmethod
    def therapy_types_all():
        from trials.models import TherapyComponentCategory

        items = TherapyComponentCategory.objects.all()
        return ordered_dict({x.code: x.title for x in items})

    def therapies_by_disease_code(self, disease_code):
        from trials.models import Therapy, DiseaseRoundTherapyConnection

        disease = self._disease_by_code(disease_code)
        connections = DiseaseRoundTherapyConnection.objects.filter(disease__in=disease).select_related('therapy')
        items = [x.therapy.id for x in connections]
        items = Therapy.objects.filter(id__in=items).all()
        return ordered_dict({x.code: x.title for x in items})

    def therapy_components_by_disease_code(self, disease_code):
        from trials.models import TherapyComponent, DiseaseRoundTherapyConnection

        disease = self._disease_by_code(disease_code)
        items = DiseaseRoundTherapyConnection.objects.filter(disease__in=disease).prefetch_related('therapy__components')
        ids = []
        for item in items:
            for comp in item.therapy.components.all():
                ids.append(comp.id)
        items = TherapyComponent.objects.filter(id__in=ids).all()
        return ordered_dict({x.code: x.title for x in items})

    def therapy_types_by_disease_code(self, disease_code):
        from trials.models import TherapyComponentCategory, DiseaseRoundTherapyConnection

        disease = self._disease_by_code(disease_code)
        items = DiseaseRoundTherapyConnection.objects.filter(disease__in=disease).prefetch_related('therapy__components__categories')
        ids = []
        for item in items:
            for comp in item.therapy.components.all():
                for cat in comp.categories.all():
                    ids.append(cat.id)
        items = TherapyComponentCategory.objects.filter(id__in=ids).all()
        return ordered_dict({x.code: x.title for x in items})

    @staticmethod
    def concomitant_medications_by_disease_code(disease_code):
        from trials.models import ConcomitantMedication

        items = ConcomitantMedication.objects.filter(concomitantmedicationdisease__disease__code=disease_code).all()
        return ordered_dict({x.code: x.title for x in items})

    @cached_property
    def statuses(self):
        return {
            'looking_for_trial': 'Looking for trial',
            'invitation_pending': 'Waiting for patient acceptance',
            'invitation_confirmed': 'Patient accepted invite',
            'invitation_rejected': 'Patient rejected invite',
            'invitation_unknown': 'Unknown',
        }

    @cached_property
    def registers(self):
        from trials.models import Trial

        out = Trial.objects.all().values('register').distinct()
        out = [x['register'] for x in out]
        out = {str(x).lower(): x for x in out}
        return {
            '': 'ALL',
            'clinicaltrials.gov': 'clinicaltrials.gov',  # make it top1
            **dict(sorted(out.items()))
        }

    @cached_property
    def study_types(self):
        return {
            '': 'ALL',
            'INTERVENTIONAL': 'Interventional',
            'OBSERVATIONAL': 'Observational',
            'INTERVENTIONAL_AND_OBSERVATIONAL': 'Interventional / Observational',
        }

    @cached_property
    def trial_types(self):
        from trials.models import TrialType

        items = TrialType.objects.order_by('title')
        return {
            '': 'ALL',
            **{x.code: x.title for x in items}
        }

    @cached_property
    def trial_purposes(self):
        from trials.models import TrialPurpose

        items = TrialPurpose.objects.order_by('title')
        return {
            '': 'ALL',
            **{x.code: x.title for x in items}
        }

    @staticmethod
    def trial_types_by_disease_code(disease_code: str):
        """Return trial types filtered by disease code."""
        from trials.models import TrialType

        if not disease_code:
            items = TrialType.objects.order_by('title')
        else:
            items = TrialType.objects.filter(
                trialtypediseaseconnection__disease__code__in=[
                    disease_code.lower(),
                    disease_code.upper()
                ]
            ).order_by('title').distinct()

        return {
            '': 'ALL',
            **{x.code: x.title for x in items}
        }

    @cached_property
    def therapies_first_line_mm(self):
        return self.therapies_by_disease_code_and_line_code('mm', 'first_line_therapy')

    @cached_property
    def therapies_first_line_fl(self):
        return self.therapies_by_disease_code_and_line_code('fl', 'first_line_therapy')

    @cached_property
    def therapies_first_line_bc(self):
        return self.therapies_by_disease_code_and_line_code('bc', 'first_line_therapy')

    @cached_property
    def therapies_second_line_mm(self):
        return self.therapies_by_disease_code_and_line_code('mm', 'second_line_therapy')

    @cached_property
    def therapies_second_line_fl(self):
        return self.therapies_by_disease_code_and_line_code('fl', 'second_line_therapy')

    @cached_property
    def therapies_second_line_bc(self):
        return self.therapies_by_disease_code_and_line_code('bc', 'second_line_therapy')

    @cached_property
    def therapies_later_mm(self):
        return self.therapies_by_disease_code_and_line_code('mm', 'later_therapy')

    @cached_property
    def therapies_later_fl(self):
        return self.therapies_by_disease_code_and_line_code('fl', 'later_therapy')

    @cached_property
    def therapies_later_bc(self):
        return self.therapies_by_disease_code_and_line_code('bc', 'later_therapy')

    @cached_property
    def therapies_first_line_cll(self):
        return self.therapies_by_disease_code_and_line_code('CLL', 'first_line_therapy')

    @cached_property
    def therapies_second_line_cll(self):
        return self.therapies_by_disease_code_and_line_code('CLL', 'second_line_therapy')

    @cached_property
    def therapies_later_cll(self):
        return self.therapies_by_disease_code_and_line_code('CLL', 'later_therapy')

    @cached_property
    def supportive_therapies_mm(self):
        return self.therapies_by_disease_code_and_line_code('mm', 'supportive_therapy')

    @cached_property
    def supportive_therapies_fl(self):
        return self.therapies_by_disease_code_and_line_code('fl', 'supportive_therapy')

    @cached_property
    def supportive_therapies_bc(self):
        return self.therapies_by_disease_code_and_line_code('bc', 'supportive_therapy')

    @cached_property
    def supportive_therapies_cll(self):
        return self.therapies_by_disease_code_and_line_code('CLL', 'supportive_therapy')

    @cached_property
    def therapies_first_line_mcl(self):
        return self.therapies_by_disease_code_and_line_code('MCL', 'first_line_therapy')

    @cached_property
    def therapies_second_line_mcl(self):
        return self.therapies_by_disease_code_and_line_code('MCL', 'second_line_therapy')

    @cached_property
    def therapies_later_mcl(self):
        return self.therapies_by_disease_code_and_line_code('MCL', 'later_therapy')

    @cached_property
    def supportive_therapies_mcl(self):
        return self.therapies_by_disease_code_and_line_code('MCL', 'supportive_therapy')

    @property
    def tumor_grades(self):
        return {
            '': 'Unknown',
            '10': 'Grade 1',
            '20': 'Grade 2',
            '30': 'Grade 3',
            '31': 'Grade 3A',
            '32': 'Grade 3B',
        }

    @property
    def flipi_scores(self):
        return {
            'age': 'Age: Greater than 60 years',
            'stage': 'Ann Arbor Stage: Stage III or IV disease',
            'hemoglobin': 'Hemoglobin Level: Less than 12 g/dL',
            'nodalAreas': 'Number of Nodal Areas Involved: More than four',
            'ldh': 'Serum Lactate Dehydrogenase (LDH) Level: Above the normal range',
        }

    @property
    def prior_therapies(self):
        values = ["None", "One line", "Two lines", "More than two lines of therapy"]
        out = {x: x for x in values}
        return {'': 'Unknown', **out}

    def planned_therapies(self, disease_code):
        from trials.models import PlannedTherapy
        items = PlannedTherapy.objects.filter(plannedtherapydiseaseconnection__disease__code__in=[disease_code.lower(), disease_code.upper()]).order_by('id')
        return {'none': 'No planned therapy', **{x.code: x.title for x in items}}

    @property
    def cytogenic_markers(self):
        from trials.models import MarkerCategory, Marker

        category = MarkerCategory.objects.get(code='cytogenic')
        markers = Marker.objects.filter(categories=category).order_by('id')

        return {'none': 'None', **{x.code: x.title for x in markers}}

    @property
    def molecular_markers(self):
        from trials.models import MarkerCategory, Marker

        category = MarkerCategory.objects.get(code='molecular')
        markers = Marker.objects.filter(categories=category).order_by('id')

        return {'none': 'None', **{x.code: x.title for x in markers}}

    @property
    def gelf_criteria_statuses(self):
        return {
            "nodalExtranodalMass": "Nodal/Extranodal Mass ≥7 cm - Any single tumor mass 7 cm or larger.",
            "multipleNodalSites": "Multiple Nodal Sites >3 cm - At least three nodes, each larger than 3 cm.",
            "systemicBSymptoms": "Systemic \"B\" Symptoms - Fever, night sweats, or weight loss.",
            "largeSplenomegaly": "Large Splenomegaly - Spleen extends below the umbilical line (≥16 cm).",
            "pleuralEffusionAscites": "Pleural Effusion/Ascites - Presence of fluid around the lungs or abdomen.",
            "organCompression": "Organ Compression - Tumors causing significant organ compression or dysfunction.",
            "boneMarrowInvolvement": "Bone Marrow Involvement - Bone marrow involvement leading to cytopenias, such as hemoglobin <10 g/dL or platelet count <100 × 10⁹/L.",
        }

    @property
    def stem_cell_transplant_history_excluded(self):
        from trials.models import StemCellTransplant
        items = StemCellTransplant.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def stem_cell_transplant_history(self):
        return {
            "": "Unknown",
            "None": "None",
            "completedASCT": "Completed ASCT",
            "eligibleForASCT": "Eligible for ASCT",
            "ineligibleForASCT": "Ineligible for ASCT",
            "completedAllogeneicSCT": "Completed Allogeneic SCT",
            "preASCT": "Pre-ASCT",
            "postASCT": "Post-ASCT",
            "neverReceivedSCT": "Never Received SCT",
            "sctIneligible": "SCT-Ineligible",
            "relapsedPostASCT": "Relapsed Post-ASCT",
            "relapsedPostAllogeneicSCT": "Relapsed Post-Allogeneic SCT",
            "completedTandemSCT": "Completed Tandem SCT",
        }

    @property
    def bone_lesions(self):
        return {
            "": "Unknown",
            "1": "1",
            "2": "2",
            "more than 2": "More than 2",
        }

    @property
    def disease(self):
        return {
            "multiple myeloma": "Multiple Myeloma",
            "follicular lymphoma": "Follicular Lymphoma",
            "breast cancer": "Breast Cancer",
            "chronic lymphocytic leukemia": "Chronic Lymphocytic Leukemia",
            "mantle cell lymphoma": "Mantle Cell Lymphoma",
        }

    @property
    def ecog_performance_status(self):
        return {
            "": "Unknown",
            "0": "0",
            "1": "1",
            "2": "2",
            "3": "3",
            "4": "4",
        }

    @property
    def karnofsky_performance_score(self):
        return {
            "": "Unknown",
            "100": "100",
            "90": "90",
            "80": "80",
            "70": "70",
            "60": "60",
            "50": "50",
            "40": "40",
            "30": "30",
            "20": "20",
            "10": "10",
        }

    @property
    def treatment_refractory_statuses(self):
        return {
            "": "Unknown",
            "notRefractory": "Not Refractory (progression halted)",
            "primaryRefractory": "Primary Refractory",
            "secondaryRefractory": "Secondary Refractory",
            "multiRefractory": "Multi-Refractory",
        }

    @property
    def therapy_outcomes(self):
        return {
            'CR': 'Complete Response (CR)',
            'sCR': 'Stringent Complete Response (sCR)',
            'VGPR': 'Very Good Partial Response (VGPR)',
            'PR': 'Partial Response (PR)',
            'MRD': 'Minimal Residual Disease (MRD) Negativity',
            'SD': 'Stable Disease (SD)',
            'PD': 'Progressive Disease (PD)',
        }

    def therapy_outcomes_by_disease_code(self, disease_code):
        """Return the clinically-applicable treatment outcomes per disease.

        Per #4137 / #83: Breast Cancer uses RECIST (CR/PR/SD/PD) and Mantle
        Cell Lymphoma uses Cheson 2014 / Lugano 2014 (also CR/PR/SD/PD).
        Neither uses IMWG-derived sCR/VGPR/MRD categories — those are
        MM-specific. MM/FL/CLL keep the full enum.
        """
        recist_outcomes = {
            'CR': self.therapy_outcomes['CR'],
            'PR': self.therapy_outcomes['PR'],
            'SD': self.therapy_outcomes['SD'],
            'PD': self.therapy_outcomes['PD'],
        }
        per_disease = {
            'BC': recist_outcomes,
            'MCL': recist_outcomes,
        }
        return per_disease.get((disease_code or '').upper(), self.therapy_outcomes)

    # ── Per-disease option-list scoping (#63 / CB #4330) ────────────────
    # Eight clinically disease-specific lists were historically exposed as
    # a single union to every patient. Each `*_by_disease_code` helper
    # returns the disease-applicable subset (the full list when the
    # patient's disease is in the applicable set, an empty/blank-only
    # dict otherwise). The "" / "Unknown" sentinel is preserved on the
    # off-disease path when the source list has one, so the form still
    # renders a valid blank option.
    #
    # Clinical scoping (verified during CB independent review, polish
    # commit dbcc1a7f): cytogenic and molecular markers apply across all
    # hematological malignancies — not just MM. Tumor burden applies to
    # FL via GELF criteria + CLL via iwCLL burden + MCL via bulky-disease.
    # BC stays correctly excluded (uses ER/PR/HER2 + HRD instead).

    _FLIPI_DISEASES = {'FL'}
    _CYTOGENIC_DISEASES = {'MM', 'FL', 'CLL', 'MCL'}
    _MOLECULAR_DISEASES = {'MM', 'FL', 'CLL', 'MCL'}
    _GELF_DISEASES = {'FL'}
    _BINET_DISEASES = {'CLL'}
    _RICHTER_DISEASES = {'CLL'}
    _TUMOR_BURDEN_DISEASES = {'FL', 'CLL', 'MCL'}
    _DISEASE_ACTIVITY_DISEASES = {'CLL'}

    @staticmethod
    def _scoped_options(full_options, disease_code, applicable_diseases):
        """Return `full_options` when disease matches; an empty/blank-only
        dict otherwise. Preserves the "" → "Unknown" sentinel if present
        so the form still renders a valid blank option."""
        if (disease_code or '').upper() in applicable_diseases:
            return full_options
        return {'': full_options['']} if '' in full_options else {}

    def flipi_scores_by_disease_code(self, disease_code):
        return self._scoped_options(self.flipi_scores, disease_code, self._FLIPI_DISEASES)

    def cytogenic_markers_by_disease_code(self, disease_code):
        return self._scoped_options(self.cytogenic_markers, disease_code, self._CYTOGENIC_DISEASES)

    def molecular_markers_by_disease_code(self, disease_code):
        return self._scoped_options(self.molecular_markers, disease_code, self._MOLECULAR_DISEASES)

    def gelf_criteria_statuses_by_disease_code(self, disease_code):
        return self._scoped_options(self.gelf_criteria_statuses, disease_code, self._GELF_DISEASES)

    def binet_stages_by_disease_code(self, disease_code):
        return self._scoped_options(self.binet_stages, disease_code, self._BINET_DISEASES)

    def richter_transformations_by_disease_code(self, disease_code):
        return self._scoped_options(self.richter_transformations, disease_code, self._RICHTER_DISEASES)

    def tumor_burdens_by_disease_code(self, disease_code):
        return self._scoped_options(self.tumor_burdens, disease_code, self._TUMOR_BURDEN_DISEASES)

    def disease_activities_by_disease_code(self, disease_code):
        return self._scoped_options(self.disease_activities, disease_code, self._DISEASE_ACTIVITY_DISEASES)

    @property
    def ethnicities(self):
        from trials.models import Ethnicity
        items = Ethnicity.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def peripheral_neuropathy_grades(self):
        return {
            '': 'Unknown',
            '0': 'Grade 0',
            '1': 'Grade 1',
            '2': 'Grade 2',
            '3': 'Grade 3',
            '4': 'Grade 4',
        }

    @property
    def progressions(self):
        return {
            '': 'Unknown',
            'active': 'Active',
            'smoldering': 'Smoldering',
        }

    @property
    def gender(self):
        return {
            '': 'Unknown',
            'M': 'Male',
            'F': 'Female',
        }

    @cached_property
    def all_countries(self):
        from trials.models import PreferredCountry
        items = PreferredCountry.objects.order_by(
            F('sort_key').asc(nulls_last=True),
            F('title')
        )
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def phases(self):
        # taken from loaded trials
        return {
            '': 'ALL',
            'EARLY_PHASE1': 'Early',
            'PHASE1': 'I',
            'PHASE2': 'II',
            'PHASE3': 'III',
            'PHASE4': 'IV'
        }

    def stages(self, disease_code: str = None):
        if str(disease_code).lower() == 'bc':
            stages = {
                '0': '0',
                'I': 'I',
                'II': 'II',
                'III': 'III (Locally Advanced)',
                'IV': 'IV (Metastatic)'
            }
        else:
            stages = {
                'I': 'I',
                'II': 'II',
                'III': 'III',
                'IV': 'IV'
            }
        return {'': 'Unknown', **stages}

    @staticmethod
    def pre_existing_conditions(with_nothing=False):
        from trials.models import PreExistingConditionCategory

        items = PreExistingConditionCategory.objects.order_by('title')
        out = {x.code: x.title for x in items}
        if with_nothing:
            return {'none': 'None', **out}
        else:
            return out

    @property
    def menopausal_status(self):
        return {
            '': 'Unknown',
            'pre_menopausal': 'pre-menopausal',
            'post_menopausal': 'post-menopausal',
        }

    @property
    def histologic_type(self):
        from trials.models import HistologicType
        items = HistologicType.objects.order_by(
            F('sort_key').asc(nulls_last=True),
            F('title')
        )
        options = {x.code: x.title for x in items}
        return {
            '': 'Unknown',
            **options,
        }

    @property
    def biopsy_grade(self):
        return {
            '': 'Unknown',
            '1': 'I',
            '2': 'II',
            '3': 'III',
        }

    @property
    def positive_negative(self):
        return {
            '': 'Unknown',
            True: 'Positive',
            False: 'Negative',
        }

    @property
    def her2_status(self):
        from trials.models import Her2Status
        items = Her2Status.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def hrd_status(self):
        from trials.models import HrdStatus
        items = HrdStatus.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def hr_status(self):
        from trials.models import HrStatus
        items = HrStatus.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def pd_l1_assay(self):
        return {
            '': 'Unknown',
            'ventana_sp142': 'VENTANA SP142',
            'dako_22c3_pharm_dx': 'Dako 22C3 pharmDx',
            'sp263': 'SP263',
            '28_8': '28-8',
            'other': 'Other',
        }

    @property
    def tumor_stages(self):
        from trials.models import TumorStage
        items = TumorStage.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def nodes_stages(self):
        from trials.models import NodesStage
        items = NodesStage.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def distant_metastasis_stages(self):
        from trials.models import DistantMetastasisStage
        items = DistantMetastasisStage.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def staging_modalities(self):
        from trials.models import StagingModality
        items = StagingModality.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def mutation_genes(self):
        from trials.models import MutationGene
        items = MutationGene.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def mutation_variants(self):
        from trials.models import MutationCode
        items = MutationCode.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def mutation_origins(self):
        from trials.models import MutationOrigin
        items = MutationOrigin.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def mutation_origins_per_gene(self):
        from trials.models import MutationGene
        genes = MutationGene.objects.order_by('id')
        out = {}
        for gene in genes:
            options = {x.code: x.title for x in gene.origins.all()}
            if options != {}:
                options = {'': 'Unknown', **options}
            out[gene.code] = self.to_value_and_label(options)
        return out

    @property
    def mutation_interpretations(self):
        from trials.models import MutationInterpretation
        items = MutationInterpretation.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def mutation_all_origins(self):
        from trials.models import MutationGene, MutationCode
        genes = MutationGene.objects.order_by('id')
        out = {}
        for gene in genes:
            origins = gene.origins.all()

            if len(origins) > 0:
                variants = MutationCode.objects.filter(gene=gene).order_by('id')

                for origin in origins:
                    code = f'{origin.code}__{gene.code}'
                    title = f'{origin.title} {gene.title}'
                    out[code] = title

                    for variant in variants:
                        code = f'{origin.code}__{variant.code}'
                        title = f'{origin.title} {variant.title}'
                        out[code] = title
        return {'': 'Unknown', **out}

    @property
    def mutation_all_interpretations(self):
        from trials.models import MutationGene, MutationInterpretation
        genes = MutationGene.objects.order_by('id')
        interpretations = MutationInterpretation.objects.order_by('id')
        out = {}
        for gene in genes:
            for interpretation in interpretations:
                code = f'{gene.code}__{interpretation.code}'
                title = f'{gene.title} {interpretation.title}'
                out[code] = title
        return {'': 'Unknown', **out}

    @property
    def all_mutation_variants(self):
        out = {}
        from trials.models import MutationGene, MutationCode
        genes = MutationGene.objects.order_by('id')
        for gene in genes:
            items = MutationCode.objects.filter(gene=gene).order_by('id')
            gene_out = {x.code: x.title for x in items}
            out[gene.code] = self.to_value_and_label({'': 'Unknown', **gene_out})
        return out

    @property
    def languages_skills(self):
        from trials.models import Language, LanguageSkillLevel
        langs = Language.objects.order_by('id')
        skill_levels = LanguageSkillLevel.objects.order_by('id')
        out = {}
        for lang in langs:
            for skill_level in skill_levels:
                code = f'{skill_level.code}__{lang.code}'
                title = f'{skill_level.title} {lang.title}'
                out[code] = title
        return {'': 'Unknown', **out}

    @property
    def toxicity_grade(self):
        from trials.models import ToxicityGrade
        items = ToxicityGrade.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def binet_stages(self):
        from trials.models import BinetStage
        items = BinetStage.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def protein_expressions(self):
        from trials.models import ProteinExpression
        items = ProteinExpression.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def richter_transformations(self):
        from trials.models import RichterTransformation
        items = RichterTransformation.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def tumor_burdens(self):
        from trials.models import TumorBurden
        items = TumorBurden.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def disease_activities(self):
        return {
            '': 'Unknown',
            'indolent': 'Indolent / Watch & Wait',
            'active_disease_iwcll': 'Active Disease requiring treatment (iwCLL)',
        }

    @property
    def disease_behaviors_mcl(self):
        return {
            '': 'Unknown',
            'indolent': 'Indolent',
            'aggressive': 'Aggressive',
        }

    @property
    def disease_subtypes_mcl(self):
        return {
            '': 'Unknown',
            'ismcn': 'In situ MCN (ISMCN)',
            'cmcl': 'Conventional nodal MCL (cMCL)',
            'nnmcl': 'Leukemic non-nodal MCL (nnMCL)',
        }

    @property
    def protein_expressions_mcl(self):
        from trials.models import ProteinExpression
        mcl_codes = [
            'cyclin_d1_plus_ve', 'cyclin_d1_minus_ve',
            'sox11_plus_ve', 'sox11_minus_ve',
            'cd10_plus_ve', 'cd10_minus_ve',
            'bcl6_plus_ve', 'bcl6_minus_ve',
        ]
        items = ProteinExpression.objects.filter(code__in=mcl_codes).order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def morphologic_variants(self):
        from trials.models import MorphologicVariant
        items = MorphologicVariant.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def mipi_risks(self):
        return {
            'low': 'Low',
            'intermediate': 'Intermediate',
            'high': 'High',
        }

    @property
    def mipi_c_risks(self):
        return {
            'low': 'Low',
            'low_intermediate': 'Low-Intermediate',
            'high_intermediate': 'High-Intermediate',
            'high': 'High',
        }

    @property
    def bulky_disease_criteria(self):
        # Canonical codes emitted by PatientInfoAttributes.bulky_disease_criteria.
        # Trial-side bulky_disease_criteria_required values must overlap with
        # this set for the matcher to fire.
        return {
            'bulky_lesion_5cm': 'Largest lesion >= 5 cm',
            'bulky_lesion_7_5cm': 'Largest lesion >= 7.5 cm',
            'bulky_lesion_10cm': 'Largest lesion >= 10 cm',
            'bulky_node_5cm': 'Largest lymph node >= 5 cm',
            'bulky_node_7_5cm': 'Largest lymph node >= 7.5 cm',
            'bulky_node_10cm': 'Largest lymph node >= 10 cm',
            'bulky_spleen_13cm': 'Spleen > 13 cm',
            'bulky_spleen_15cm': 'Spleen > 15 cm',
            'bulky_spleen_20cm_gt': 'Spleen > 20 cm',
            'bulky_spleen_20cm_gte': 'Spleen >= 20 cm',
        }

    @property
    def high_risk_mcl_criteria(self):
        from trials.models import HighRiskMclCriteria
        items = HighRiskMclCriteria.objects.order_by('id')
        return {x.code: x.title for x in items}

    @property
    def er_statuses(self):
        from trials.models import EstrogenReceptorStatus
        items = EstrogenReceptorStatus.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    @property
    def pr_statuses(self):
        from trials.models import ProgesteroneReceptorStatus
        items = ProgesteroneReceptorStatus.objects.order_by('id')
        out = {x.code: x.title for x in items}
        return {'': 'Unknown', **out}

    def all_options(self):
        cache_key = self.cache_key()
        result = cache.get(cache_key)
        if result is None:
            result = self.get_all_options()
            cache.set(cache_key, result, timeout=3600)  # Cache for 1 hour
        return result

    def get_all_options(self):
        return {
            'statuses': {
                'options': self.to_value_and_label(self.statuses)
            },
            'register': {
                'options': self.to_value_and_label(self.registers)
            },
            'studyType': {
                'options': self.to_value_and_label(self.study_types)
            },
            'trialType': {
                'options': self.to_value_and_label(self.trial_types)
            },
            'trialPurpose': {
                'options': self.to_value_and_label(self.trial_purposes)
            },
            'tumorGrade': {
                'options': self.to_value_and_label(self.tumor_grades)
            },
            'flipiScore': {
                'options': self.to_value_and_label(self.flipi_scores)
            },
            # Per-disease FLIPI enum (#63). FL-only clinically.
            'flipiScoreMm': {
                'options': self.to_value_and_label(self.flipi_scores_by_disease_code('MM'))
            },
            'flipiScoreFl': {
                'options': self.to_value_and_label(self.flipi_scores_by_disease_code('FL'))
            },
            'flipiScoreBc': {
                'options': self.to_value_and_label(self.flipi_scores_by_disease_code('BC'))
            },
            'flipiScoreCll': {
                'options': self.to_value_and_label(self.flipi_scores_by_disease_code('CLL'))
            },
            'flipiScoreMcl': {
                'options': self.to_value_and_label(self.flipi_scores_by_disease_code('MCL'))
            },
            'priorTherapy': {
                'options': self.to_value_and_label(self.prior_therapies)
            },
            'allCountries': {
                'options': self.to_value_and_label(self.all_countries)
            },
            'phases': {
                'options': self.to_value_and_label(self.phases)
            },
            'therapyOutcome': {
                'options': self.to_value_and_label(self.therapy_outcomes)
            },
            # Per-disease outcome enums (#4137). Keep `therapyOutcome` above as
            # the union for back-compat with callers that haven't been updated.
            'therapyOutcomeMm': {
                'options': self.to_value_and_label(self.therapy_outcomes_by_disease_code('MM'))
            },
            'therapyOutcomeFl': {
                'options': self.to_value_and_label(self.therapy_outcomes_by_disease_code('FL'))
            },
            'therapyOutcomeBc': {
                'options': self.to_value_and_label(self.therapy_outcomes_by_disease_code('BC'))
            },
            'therapyOutcomeCll': {
                'options': self.to_value_and_label(self.therapy_outcomes_by_disease_code('CLL'))
            },
            'therapyOutcomeMcl': {
                'options': self.to_value_and_label(self.therapy_outcomes_by_disease_code('MCL'))
            },
            'ethnicity': {
                'options': self.to_value_and_label(self.ethnicities)
            },
            'peripheralNeuropathyGrade': {
                'options': self.to_value_and_label(self.peripheral_neuropathy_grades)
            },
            'plannedTherapiesMm': {
                'options': self.to_value_and_label(self.planned_therapies('mm'))
            },
            'plannedTherapiesFl': {
                'options': self.to_value_and_label(self.planned_therapies('fl'))
            },
            'plannedTherapiesBc': {
                'options': self.to_value_and_label(self.planned_therapies('bc'))
            },
            'plannedTherapiesCll': {
                'options': self.to_value_and_label(self.planned_therapies('CLL'))
            },
            'plannedTherapiesMcl': {
                'options': self.to_value_and_label(self.planned_therapies('MCL'))
            },
            'cytogenicMarkers': {
                'options': self.to_value_and_label(self.cytogenic_markers)
            },
            # Per-disease cytogenic markers (#63). Hematological-only.
            'cytogenicMarkersMm': {
                'options': self.to_value_and_label(self.cytogenic_markers_by_disease_code('MM'))
            },
            'cytogenicMarkersFl': {
                'options': self.to_value_and_label(self.cytogenic_markers_by_disease_code('FL'))
            },
            'cytogenicMarkersBc': {
                'options': self.to_value_and_label(self.cytogenic_markers_by_disease_code('BC'))
            },
            'cytogenicMarkersCll': {
                'options': self.to_value_and_label(self.cytogenic_markers_by_disease_code('CLL'))
            },
            'cytogenicMarkersMcl': {
                'options': self.to_value_and_label(self.cytogenic_markers_by_disease_code('MCL'))
            },
            'molecularMarkers': {
                'options': self.to_value_and_label(self.molecular_markers)
            },
            # Per-disease molecular markers (#63). Hematological-only.
            'molecularMarkersMm': {
                'options': self.to_value_and_label(self.molecular_markers_by_disease_code('MM'))
            },
            'molecularMarkersFl': {
                'options': self.to_value_and_label(self.molecular_markers_by_disease_code('FL'))
            },
            'molecularMarkersBc': {
                'options': self.to_value_and_label(self.molecular_markers_by_disease_code('BC'))
            },
            'molecularMarkersCll': {
                'options': self.to_value_and_label(self.molecular_markers_by_disease_code('CLL'))
            },
            'molecularMarkersMcl': {
                'options': self.to_value_and_label(self.molecular_markers_by_disease_code('MCL'))
            },
            'gelfCriteriaStatus': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses)
            },
            # Per-disease GELF criteria (#63). FL-only clinically.
            'gelfCriteriaStatusMm': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses_by_disease_code('MM'))
            },
            'gelfCriteriaStatusFl': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses_by_disease_code('FL'))
            },
            'gelfCriteriaStatusBc': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses_by_disease_code('BC'))
            },
            'gelfCriteriaStatusCll': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses_by_disease_code('CLL'))
            },
            'gelfCriteriaStatusMcl': {
                'options': self.to_value_and_label(self.gelf_criteria_statuses_by_disease_code('MCL'))
            },
            'progression': {
                'options': self.to_value_and_label(self.progressions)
            },
            'stemCellTransplantHistory': {
                'options': self.to_value_and_label(self.stem_cell_transplant_history)
            },
            'stemCellTransplantHistoryExcluded': {
                'options': self.to_value_and_label(self.stem_cell_transplant_history_excluded)
            },
            'boneLesions': {
                'options': self.to_value_and_label(self.bone_lesions)
            },
            'disease': {
                'options': self.to_value_and_label(self.disease)
            },
            'gender': {
                'options': self.to_value_and_label(self.gender)
            },
            'ecogPerformanceStatus': {
                'options': self.to_value_and_label(self.ecog_performance_status)
            },
            'karnofskyPerformanceScore': {
                'options': self.to_value_and_label(self.karnofsky_performance_score)
            },
            'treatmentRefractoryStatus': {
                'options': self.to_value_and_label(self.treatment_refractory_statuses)
            },
            'preExistingConditionCategories': {
                'options': self.to_value_and_label(self.pre_existing_conditions())
            },
            'upreExistingConditionCategories': {
                'options': self.to_value_and_label(self.pre_existing_conditions(with_nothing=True))
            },
            'therapiesAll': {
                'options': self.to_value_and_label(self.therapies_all())
            },
            'therapyComponentsAll': {
                'options': self.to_value_and_label(self.therapy_components_all())
            },
            'therapyTypesAll': {
                'options': self.to_value_and_label(self.therapy_types_all())
            },
            'therapiesMm': {
                'options': self.to_value_and_label(self.therapies_by_disease_code('mm'))
            },
            'therapiesFl': {
                'options': self.to_value_and_label(self.therapies_by_disease_code('fl'))
            },
            'therapiesBc': {
                'options': self.to_value_and_label(self.therapies_by_disease_code('bc'))
            },
            'therapiesCll': {
                'options': self.to_value_and_label(self.therapies_by_disease_code('CLL'))
            },
            'therapiesMcl': {
                'options': self.to_value_and_label(self.therapies_by_disease_code('MCL'))
            },
            'therapyComponentsMm': {
                'options': self.to_value_and_label(self.therapy_components_by_disease_code('mm'))
            },
            'therapyComponentsFl': {
                'options': self.to_value_and_label(self.therapy_components_by_disease_code('fl'))
            },
            'therapyComponentsBc': {
                'options': self.to_value_and_label(self.therapy_components_by_disease_code('bc'))
            },
            'therapyTypesMm': {
                'options': self.to_value_and_label(self.therapy_types_by_disease_code('mm'))
            },
            'therapyTypesFl': {
                'options': self.to_value_and_label(self.therapy_types_by_disease_code('fl'))
            },
            'therapyTypesBc': {
                'options': self.to_value_and_label(self.therapy_types_by_disease_code('bc'))
            },
            'therapiesFirstLineMm': {
                'options': self.to_value_and_label(self.therapies_first_line_mm)
            },
            'therapiesFirstLineFl': {
                'options': self.to_value_and_label(self.therapies_first_line_fl)
            },
            'therapiesFirstLineBc': {
                'options': self.to_value_and_label(self.therapies_first_line_bc)
            },
            'therapiesFirstLineCll': {
                'options': self.to_value_and_label(self.therapies_first_line_cll)
            },
            'therapiesSecondLineMm': {
                'options': self.to_value_and_label(self.therapies_second_line_mm)
            },
            'therapiesSecondLineFl': {
                'options': self.to_value_and_label(self.therapies_second_line_fl)
            },
            'therapiesSecondLineBc': {
                'options': self.to_value_and_label(self.therapies_second_line_bc)
            },
            'therapiesSecondLineCll': {
                'options': self.to_value_and_label(self.therapies_second_line_cll)
            },
            'therapiesLaterLineMm': {
                'options': self.to_value_and_label(self.therapies_later_mm)
            },
            'therapiesLaterLineFl': {
                'options': self.to_value_and_label(self.therapies_later_fl)
            },
            'therapiesLaterLineBc': {
                'options': self.to_value_and_label(self.therapies_later_bc)
            },
            'therapiesLaterLineCll': {
                'options': self.to_value_and_label(self.therapies_later_cll)
            },
            'therapiesFirstLineMcl': {
                'options': self.to_value_and_label(self.therapies_first_line_mcl)
            },
            'therapiesSecondLineMcl': {
                'options': self.to_value_and_label(self.therapies_second_line_mcl)
            },
            'therapiesLaterLineMcl': {
                'options': self.to_value_and_label(self.therapies_later_mcl)
            },
            'supportiveTherapiesMm': {
                'options': self.to_value_and_label(self.supportive_therapies_mm)
            },
            'supportiveTherapiesFl': {
                'options': self.to_value_and_label(self.supportive_therapies_fl)
            },
            'supportiveTherapiesBc': {
                'options': self.to_value_and_label(self.supportive_therapies_bc)
            },
            'supportiveTherapiesCll': {
                'options': self.to_value_and_label(self.supportive_therapies_cll)
            },
            'supportiveTherapiesMcl': {
                'options': self.to_value_and_label(self.supportive_therapies_mcl)
            },
            'concomitantMedicationsMm': {
                'options': self.to_value_and_label(self.concomitant_medications_by_disease_code('MM'))
            },
            'concomitantMedicationsFl': {
                'options': self.to_value_and_label(self.concomitant_medications_by_disease_code('FL'))
            },
            'concomitantMedicationsBc': {
                'options': self.to_value_and_label(self.concomitant_medications_by_disease_code('BC'))
            },
            'concomitantMedicationsMcl': {
                'options': self.to_value_and_label(self.concomitant_medications_by_disease_code('MCL'))
            },
            'menopausalStatus': {
                'options': self.to_value_and_label(self.menopausal_status)
            },
            'histologicType': {
                'options': self.to_value_and_label(self.histologic_type)
            },
            'biopsyGrade': {
                'options': self.to_value_and_label(self.biopsy_grade)
            },
            'tumorStages': {
                'options': self.to_value_and_label(self.tumor_stages)
            },
            'nodesStages': {
                'options': self.to_value_and_label(self.nodes_stages)
            },
            'distantMetastasisStages': {
                'options': self.to_value_and_label(self.distant_metastasis_stages)
            },
            'stagingModalities': {
                'options': self.to_value_and_label(self.staging_modalities)
            },
            'geneticMutationGenes': {
                'options': self.to_value_and_label(self.mutation_genes)
            },
            'geneticMutationVariants': {
                'options': self.to_value_and_label(self.mutation_variants)
            },
            'geneticMutationAllVariants': {
                'options': self.all_mutation_variants
            },
            'geneticMutationOrigins': {
                'options': self.to_value_and_label(self.mutation_origins)
            },
            'geneticMutationOriginsPerGene': {
                'options': self.mutation_origins_per_gene
            },
            'geneticMutationInterpretations': {
                'options': self.to_value_and_label(self.mutation_interpretations)
            },
            'geneticMutationAllOrigins': {
                'options': self.to_value_and_label(self.mutation_all_origins)
            },
            'geneticMutationAllInterpretations': {
                'options': self.to_value_and_label(self.mutation_all_interpretations)
            },
            'pdL1Assay': {
                'options': self.to_value_and_label(self.pd_l1_assay)
            },
            'positiveNegative': {
                'options': self.to_value_and_label(self.positive_negative)
            },
            'her2Status': {
                'options': self.to_value_and_label(self.her2_status)
            },
            'hrdStatus': {
                'options': self.to_value_and_label(self.hrd_status)
            },
            'hrStatus': {
                'options': self.to_value_and_label(self.hr_status)
            },
            'estrogenReceptorStatus': {
                'options': self.to_value_and_label(self.er_statuses)
            },
            'progesteroneReceptorStatus': {
                'options': self.to_value_and_label(self.pr_statuses)
            },
            'stagesMm': {
                'options': self.to_value_and_label(self.stages('mm'))
            },
            'stagesFl': {
                'options': self.to_value_and_label(self.stages('fl'))
            },
            'stagesBc': {
                'options': self.to_value_and_label(self.stages('bc'))
            },
            'stagesCll': {
                'options': self.to_value_and_label(self.stages('CLL'))
            },
            'stagesMcl': {
                'options': self.to_value_and_label(self.stages('MCL'))
            },
            'languagesSkills': {
                'options': self.to_value_and_label(self.languages_skills)
            },
            'toxicityGrade': {
                'options': self.to_value_and_label(self.toxicity_grade)
            },
            'binetStages': {
                'options': self.to_value_and_label(self.binet_stages)
            },
            # Per-disease Binet stages (#63). CLL-only clinically.
            'binetStagesMm': {
                'options': self.to_value_and_label(self.binet_stages_by_disease_code('MM'))
            },
            'binetStagesFl': {
                'options': self.to_value_and_label(self.binet_stages_by_disease_code('FL'))
            },
            'binetStagesBc': {
                'options': self.to_value_and_label(self.binet_stages_by_disease_code('BC'))
            },
            'binetStagesCll': {
                'options': self.to_value_and_label(self.binet_stages_by_disease_code('CLL'))
            },
            'binetStagesMcl': {
                'options': self.to_value_and_label(self.binet_stages_by_disease_code('MCL'))
            },
            'proteinExpressions': {
                'options': self.to_value_and_label(self.protein_expressions)
            },
            'richterTransformations': {
                'options': self.to_value_and_label(self.richter_transformations)
            },
            # Per-disease Richter transformations (#63). CLL-only clinically.
            'richterTransformationsMm': {
                'options': self.to_value_and_label(self.richter_transformations_by_disease_code('MM'))
            },
            'richterTransformationsFl': {
                'options': self.to_value_and_label(self.richter_transformations_by_disease_code('FL'))
            },
            'richterTransformationsBc': {
                'options': self.to_value_and_label(self.richter_transformations_by_disease_code('BC'))
            },
            'richterTransformationsCll': {
                'options': self.to_value_and_label(self.richter_transformations_by_disease_code('CLL'))
            },
            'richterTransformationsMcl': {
                'options': self.to_value_and_label(self.richter_transformations_by_disease_code('MCL'))
            },
            'tumorBurdens': {
                'options': self.to_value_and_label(self.tumor_burdens)
            },
            # Per-disease tumor burdens (#63). FL/CLL/MCL clinically.
            'tumorBurdensMm': {
                'options': self.to_value_and_label(self.tumor_burdens_by_disease_code('MM'))
            },
            'tumorBurdensFl': {
                'options': self.to_value_and_label(self.tumor_burdens_by_disease_code('FL'))
            },
            'tumorBurdensBc': {
                'options': self.to_value_and_label(self.tumor_burdens_by_disease_code('BC'))
            },
            'tumorBurdensCll': {
                'options': self.to_value_and_label(self.tumor_burdens_by_disease_code('CLL'))
            },
            'tumorBurdensMcl': {
                'options': self.to_value_and_label(self.tumor_burdens_by_disease_code('MCL'))
            },
            'diseaseActivities': {
                'options': self.to_value_and_label(self.disease_activities)
            },
            # Per-disease disease activities (#63). CLL-only clinically.
            'diseaseActivitiesMm': {
                'options': self.to_value_and_label(self.disease_activities_by_disease_code('MM'))
            },
            'diseaseActivitiesFl': {
                'options': self.to_value_and_label(self.disease_activities_by_disease_code('FL'))
            },
            'diseaseActivitiesBc': {
                'options': self.to_value_and_label(self.disease_activities_by_disease_code('BC'))
            },
            'diseaseActivitiesCll': {
                'options': self.to_value_and_label(self.disease_activities_by_disease_code('CLL'))
            },
            'diseaseActivitiesMcl': {
                'options': self.to_value_and_label(self.disease_activities_by_disease_code('MCL'))
            },
            'diseaseBehaviorsMcl': {
                'options': self.to_value_and_label(self.disease_behaviors_mcl)
            },
            'diseaseSubtypesMcl': {
                'options': self.to_value_and_label(self.disease_subtypes_mcl)
            },
            'proteinExpressionsMcl': {
                'options': self.to_value_and_label(self.protein_expressions_mcl)
            },
            'morphologicVariants': {
                'options': self.to_value_and_label(self.morphologic_variants)
            },
            'mipiRisks': {
                'options': self.to_value_and_label(self.mipi_risks)
            },
            'mipiCRisks': {
                'options': self.to_value_and_label(self.mipi_c_risks)
            },
            'bulkyDiseaseCriteria': {
                'options': self.to_value_and_label(self.bulky_disease_criteria)
            },
            'highRiskMclCriteria': {
                'options': self.to_value_and_label(self.high_risk_mcl_criteria)
            },
        }
