from trials.models import Disease, HighRiskMclCriteria, MorphologicVariant, ProteinExpression


MCL_PROTEIN_EXPRESSION_DATA = [
    {'code': 'cyclin_d1_plus_ve', 'title': 'Cyclin D1 +ve'},
    {'code': 'cyclin_d1_minus_ve', 'title': 'Cyclin D1 -ve'},
    {'code': 'sox11_plus_ve', 'title': 'SOX11 +ve'},
    {'code': 'sox11_minus_ve', 'title': 'SOX11 -ve'},
    {'code': 'cd10_plus_ve', 'title': 'CD10 +ve'},
    {'code': 'cd10_minus_ve', 'title': 'CD10 -ve'},
    {'code': 'bcl6_plus_ve', 'title': 'BCL6 +ve'},
    {'code': 'bcl6_minus_ve', 'title': 'BCL6 -ve'},
]

MORPHOLOGIC_VARIANT_DATA = [
    {'code': 'classic', 'title': 'Classic'},
    {'code': 'blastoid', 'title': 'Blastoid'},
    {'code': 'pleomorphic', 'title': 'Pleomorphic'},
]

# High-risk MCL criteria vocabulary. Mirrors CB data migrations
# 0370_mcl_high_risk_criteria_data (base) + 0381_mcl_high_risk_criteria_granularity
# (notch1_or_2 / complex_karyotype_strict, #4406), collapsed into one loader.
HIGH_RISK_MCL_CRITERIA_DATA = [
    {'code': 'tp53_mutation', 'title': 'TP53 Mutation'},
    {'code': 'kmt2d_mutation', 'title': 'KMT2D Mutation'},
    {'code': 'nsd2_mutation', 'title': 'NSD2 Mutation'},
    {'code': 'notch1_mutation', 'title': 'NOTCH1 Mutation'},
    {'code': 'notch2_mutation', 'title': 'NOTCH2 Mutation'},
    {'code': 'cdkn2a_alteration', 'title': 'CDKN2A Alteration'},
    {'code': 'smarca4_mutation', 'title': 'SMARCA4 Mutation'},
    {'code': 'ccnd1_alteration', 'title': 'CCND1 Alteration'},
    {'code': 'bcl2_amplification', 'title': 'BCL2 Amplification'},
    {'code': 'del17p', 'title': 'del(17p)'},
    {'code': 'complex_karyotype', 'title': 'Complex Karyotype ≥3 abnormalities'},
    {'code': 'myc_rearrangement', 'title': 'MYC Rearrangement'},
    {'code': 'p53_ihc_gte_50', 'title': 'p53 Expression ≥50%'},
    {'code': 'blastoid', 'title': 'Blastoid Morphology'},
    {'code': 'pleomorphic', 'title': 'Pleomorphic Morphology'},
    {'code': 'ki67_gt_30', 'title': 'Ki67 >30%'},
    {'code': 'ki67_gte_30', 'title': 'Ki67 ≥30%'},
    {'code': 'ki67_gt_50', 'title': 'Ki67 >50%'},
    {'code': 'ki67_gte_50', 'title': 'Ki67 ≥50%'},
    {'code': 'high_mipi', 'title': 'High MIPI Score ≥6.2'},
    {'code': 'high_mipi_simplified', 'title': 'High Simplified MIPI (sMIPI) Score ≥6'},
    {'code': 'mipi_c_high', 'title': 'MIPI-c High (High MIPI + Ki67 ≥30%)'},
    {'code': 'mipi_c_high_int_high_mipi', 'title': 'MIPI-c High-Intermediate (High MIPI + Ki67 <30%)'},
    {'code': 'mipi_c_high_int_int_mipi', 'title': 'MIPI-c High-Intermediate (Intermediate MIPI + Ki67 ≥30%)'},
    {'code': 'lesion_gte_5cm', 'title': 'Bulky Disease: Lesion ≥5 cm'},
    {'code': 'lesion_gte_7_5cm', 'title': 'Bulky Disease: Lesion ≥7.5 cm'},
    {'code': 'lesion_gt_10cm', 'title': 'Bulky Disease: Lesion >10 cm'},
    {'code': 'node_gte_5cm', 'title': 'Bulky Disease: Node ≥5 cm'},
    {'code': 'node_gte_7_5cm', 'title': 'Bulky Disease: Node ≥7.5 cm'},
    {'code': 'node_gte_10cm', 'title': 'Bulky Disease: Node ≥10 cm'},
    {'code': 'spleen_gte_13cm', 'title': 'Bulky Disease: Spleen ≥13 cm'},
    {'code': 'spleen_gte_15cm', 'title': 'Bulky Disease: Spleen ≥15 cm'},
    {'code': 'spleen_gte_20cm', 'title': 'Bulky Disease: Spleen ≥20 cm'},
    {'code': 'lymphocytosis_gte_50k', 'title': 'Lymphocytosis ≥50,000 cells/µL'},
    # Granularity additions (CB migration 0381) — kept LAST so insertion-order
    # (value_options orders by id) matches CB's dropdown ordering.
    {'code': 'notch1_or_2', 'title': 'NOTCH1 or NOTCH2 Mutation'},
    {'code': 'complex_karyotype_strict', 'title': 'Complex Karyotype ≥3 abnormalities (excluding t(11;14))'},
]


class LoadMclOptions:
    """Seed MCL-specific reference data: disease row, protein expression markers,
    morphologic variants, high-risk MCL criteria. Mirrors CB's MCL data migrations."""

    def load_all(self):
        self.load_disease()
        self.load_protein_expressions()
        self.load_morphologic_variants()
        self.load_high_risk_mcl_criteria()

    def load_disease(self):
        Disease.objects.update_or_create(
            code='MCL',
            defaults={'title': 'Mantle Cell Lymphoma'},
        )

    def load_protein_expressions(self):
        for entry in MCL_PROTEIN_EXPRESSION_DATA:
            ProteinExpression.objects.update_or_create(
                code=entry['code'],
                defaults={'title': entry['title']},
            )

    def load_morphologic_variants(self):
        for entry in MORPHOLOGIC_VARIANT_DATA:
            MorphologicVariant.objects.update_or_create(
                code=entry['code'],
                defaults={'title': entry['title']},
            )

    def load_high_risk_mcl_criteria(self):
        for entry in HIGH_RISK_MCL_CRITERIA_DATA:
            HighRiskMclCriteria.objects.update_or_create(
                code=entry['code'],
                defaults={'title': entry['title']},
            )
