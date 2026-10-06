class GeneticMutations:
    @staticmethod
    def mutation_genes(pi_genetic_mutations):
        items = [x.get('gene') for x in pi_genetic_mutations if x.get('gene')]
        return sorted(list(set(items)))

    @staticmethod
    def mutation_variants(pi_genetic_mutations):
        items = [x.get('variant') for x in pi_genetic_mutations if x.get('variant')]
        return sorted(list(set(items)))

    @staticmethod
    def mutation_origins(pi_genetic_mutations):
        out = []
        for item in pi_genetic_mutations:
            if item.get('origin'):
                if item.get('gene'):
                    out.append(f"{item.get('origin')}__{item.get('gene')}")
                if item.get('variant'):
                    out.append(f"{item.get('origin')}__{item.get('variant')}")
        return sorted(list(set(out)))

    @staticmethod
    def mutation_interpretations(pi_genetic_mutations):
        out = []
        for item in pi_genetic_mutations:
            if item.get('interpretation'):
                if item.get('gene'):
                    out.append(f"{item.get('gene')}__{item.get('interpretation')}")
        return sorted(list(set(out)))
