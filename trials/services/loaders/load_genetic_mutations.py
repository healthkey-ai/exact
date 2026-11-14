from trials.models import *
from trials.services.genetic_mutation_catalog import V422DEL_CODE, V422DEL_TITLE


class LoadGeneticMutations:
    def load_all(self, skip_genes_origins=True):
        self.load_genes()
        self.load_origins()
        self.load_interpretations()
        self.load_mutation_codes()
        if not skip_genes_origins:
            self.load_connect_genes_with_origins()

    def load_genes(self):
        data = {
            "BRCA1".lower(): "BRCA1",
            "BRCA2".lower(): "BRCA2",
            "TP53".lower(): "TP53",
            "PIK3CA".lower(): "PIK3CA",
            "ESR1".lower(): "ESR1"
        }

        for code in data.keys():
            MutationGene.objects.update_or_create(code=code, defaults={'title': data[code]})

    def load_origins(self):
        data = {
            "somatic": "somatic",
            "germline": "germline",
        }

        for code in data.keys():
            MutationOrigin.objects.update_or_create(code=code, defaults={'title': data[code]})

    def load_connect_genes_with_origins(self):
        data = {
            "BRCA1".lower(): ["somatic", "germline"],
            "BRCA2".lower(): ["somatic", "germline"],
            "TP53".lower(): ["somatic", "germline"],
        }

        for code in data.keys():
            gene = MutationGene.objects.get(code=code)
            origins = MutationOrigin.objects.filter(code__in=data[code]).all()
            gene.origins.set(origins or [])

    def load_interpretations(self):
        data = {
            "pathogenic": "Pathogenic",
            "likely_pathogenic": "Likely pathogenic",
            "vus": "Variant of Uncertain Significance (VUS)",
            "likely_benign": "Likely benign",
            "benign": "Benign",
            "no_mutation_detected": "No mutation detected",
        }

        for code in data.keys():
            MutationInterpretation.objects.update_or_create(code=code, defaults={'title': data[code]})

    def load_mutation_codes(self):
        def value_to_code(value: str) -> str:
            return value.replace('>', '_').replace(' ', '_').lower()

        def load_mutations_group(gene_code: str, mutation_titles: list):
            gene = MutationGene.objects.get(code=gene_code)
            for entry in mutation_titles:
                # A (code, title) pair pins the stored identity: the label may
                # change without minting a new option code. Plain strings keep
                # the historical label -> code derivation.
                if isinstance(entry, tuple):
                    m_code, m_title = entry
                    # The corrected label may already be held by another code
                    # (curator edit or a partially released build). EXACT's
                    # PROMOP adapter always emits the canonical code, so
                    # converge on that identity instead of adopting a code it
                    # cannot produce; only the row is removed, nothing rewrites
                    # a trial.
                    MutationCode.objects.filter(title=m_title).exclude(
                        code=m_code,
                    ).delete()
                else:
                    m_code, m_title = value_to_code(entry), entry
                MutationCode.objects.update_or_create(code=m_code, defaults={'title': m_title, 'gene': gene})

        brca1 = [
            'c.1135_1136insA (1135insA)',
            'c.1294_1333del40 (1294del40)',
            'c.68_69delAG (185delAG)',
            'c.3700_3704delGTAAA (3819delGTAAA)',
            'c.3786_3789delGTCT (3875delGTCT)',
            'c.4035delA (4153delA)',
            'c.5266dupC (5382insC)',
            'c.181T>G (p.C61G)',
            'c.1687C>T (p.Q563X)',
            'c.4330C>T (p.R1443X)',
            'c.211G>A (p.R71G)',
            'c.314A>G (p.Y105C)',
            'c.5096G>A',
            'c.4035delAAGA'
        ]

        brca2 = [
            'c.10095delC (3366delC)',
            'c.10150C>T',
            'c.10204C>T (R3402X)',
            'c.10230_10233delAGAA',
            'c.10247A>G',
            'c.10276C>T',
            'c.10323C>T (R3442X)',
            'c.10350C>A (Y3450X)',
            'c.10370A>G (N3457S)',
            'c.10411C>T',
            'c.10453C>T (R3485X)',
            'c.10509C>A (Y3503X)',
            'c.10580G>A',
            'c.10606C>T (R3536X)',
            'c.10632_10633delAG',
            'c.10647delC (3550delC)',
            'c.10692_10693insA',
            'c.10740C>T (R3572X)',
            'c.10776_10777delAG',
            'c.10810C>T (R3604X)',
            'c.10824_10825delCT (3609delCT)',
            'c.10830_10831delAA (3611delAA)',
            'c.10844C>T (R3615X)',
            'c.2338C>T (Q780*)',
            'c.3036_3039del (3036del4)',
            'c.3326_3327insA (3326insA)',
            'c.5950_5951delCT (5950delCT)',
            'c.5946delT (6174delT)',
            'c.6174delT (6174delT)',
            'c.6503_6504delTT (6503delTT)',
            'c.7008‑1G>A',
            'c.7558C>T',
            'c.7845+1G>A (7845+1G>A)',
            'c.7617+1G>A',
            'c.7913_7917delTTAAA',
            'c.7975A>T',
            'c.8168A>G',
            'c.8488‑1G>A',
            'c.8537_8538delAG',
            'c.8572C>T',
            'c.873delT (873delT)',
            'c.8755delG',
            'c.886_887delGT (886delGT)',
            'c.999_1003del (999del5)',
            'c.9097_9098insA (3033insA)',
            'c.9117G>A',
            'c.9154C>T',
            'c.9235delG (3079delG)',
            'c.9265+1G>A',
            'c.9308G>A',
            'c.9382C>T (R3128X)',
            'c.9501+1G>A',
            'c.9610C>T (R3204X)',
            'c.9631delC',
            'c.9653delA',
            'c.9700C>T',
            'c.9816delC (3272delC)',
            'c.9852delT (3285delT)',
            'c.9924C>A (Y3308X)',
            'c.9976A>T (K3326X)'
        ]

        tp53 = [
            'c.404G>A (C135Y)',
            'c.853G>A (E285K)',
            'c.733G>A (G245S)',
            'c.451C>T (P151S)',
            'c.473G>T (R158L)',
            'c.524G>A (R175H)',
            'c.586C>T (R196*)',
            'c.743G>A (R248Q)',
            'c.742C>T (R248W)',
            'c.747G>T (R249S)',
            'c.818G>A (R273H)',
            'c.844C>T (R282W)',
            'c.469G>T (V157F)',
            'c.659A>G (Y220C)',
            'c.487T>A (Y163N)',
            'c.841G>C (D281H)',
            'c.577C>T (H193Y)',
            'c.535C>T (H179Y)',
            'c.638G>T (R213L)',
            'c.637C>T (R213*)',
            'c.638G>A (R213Q)',
            'c.329G>T (R110L)',
            'c.476C>T (A159V)',
            'c.461G>T (G154V)',
            'c.499C>A (Q167K)',
            'c.584T>C (I195T)',
            'c.701A>G (Y234C)',
            'c.488A>G (Y163C)',
            'c.332T>A (L111Q)',
            'c.833C>T (P278L)',
            'c.817C>T (R273C)',
            'c.329G>A (R110H)',
            'c.404G>T (C135F)',
            'c.818G>T (R273L)',
            'c.844C>G (R282G)',
            'c.587G>A (R196Q)',
            'c.473G>A (R158H)',
            'c.332T>G (L111R)',
            'c.916C>T (R306*)',
            'c.713G>T (C238F)',
            'c.797G>T (G266V)',
            'c.917G>A (R306Q)',
            'c.874A>G (K292E)',
        ]

        pik3ca = [
            'c.1624G>A (p.E542K)',
            'c.1625A>G (p.E542G)',
            'c.1633G>A (p.E545K)',
            'c.1633G>C (p.E545Q)',
            'c.1637A>C (p.Q546P)',
            'c.1637A>G (p.Q546R)',
            'c.1636C>A (p.Q546K)',
            'c.3140A>C (p.H1047P)',
            'c.3140A>G (p.H1047R)',
            'c.3140A>T (p.H1047L)',
            'c.3142C>T (p.H1048Y)',
            'c.3143A>G (p.H1048R)',
            'c.3144T>G (p.H1048Q)',
            'c.3145G>A (p.G1049S)',
            'c.3145G>C (p.G1049R)',
        ]

        esr1 = [
            'c.1138G>C (E380Q)',
            (V422DEL_CODE, V422DEL_TITLE),
            'c.1387T>C (S463P)',
            'c.1604C>A (P535H)',
            'c.1607T>A (L536H)',
            'c.1607T>C (L536P)',
            'c.1607T>G (L536R)',
            'c.1607_1608delinsAG (L536Q)',
            'c.1610A>C (Y537S)',
            'c.1609T>A (Y537N)',
            'c.1610A>G (Y537C)',
            'c.1613A>G (D538G)',
            'c.908A>G (K303R)'
        ]

        load_mutations_group('brca1', brca1)
        load_mutations_group('brca2', brca2)
        load_mutations_group('tp53', tp53)
        load_mutations_group('pik3ca', pik3ca)
        load_mutations_group('esr1', esr1)
