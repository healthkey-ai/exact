class PatientInfoFlipyScore:
    @staticmethod
    def scope_by_options(flipi_score_options):
        avail_options = ['age', 'stage', 'hemoglobin', 'nodalAreas', 'ldh']
        items = str(flipi_score_options).split(',')
        items = [x.strip() for x in items]
        res = len(set(items).intersection(avail_options))
        return res if res > 0 else None
