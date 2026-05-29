from trials.models import Disease, MorphologicVariant, ProteinExpression


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


class LoadMclOptions:
    """Seed MCL-specific reference data: disease row, protein expression markers,
    morphologic variants. Mirrors CB's 0352_mcl_foundation data migration."""

    def load_all(self):
        self.load_disease()
        self.load_protein_expressions()
        self.load_morphologic_variants()

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
