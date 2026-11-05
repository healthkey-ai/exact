from exact_matching.omop.genomics import gene_concept_id, gene_symbols_for_concept_ids


def _concept_key(value):
    """A positive OMOP concept id as a string, or None."""
    if isinstance(value, bool) or value in (None, ''):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return str(number) if number > 0 else None


class GeneticMutations:
    @staticmethod
    def mutation_genes(pi_genetic_mutations):
        items = [x.get('gene') for x in pi_genetic_mutations if x.get('gene')]
        return sorted(list(set(items)))

    @staticmethod
    def mutation_gene_concept_ids(pi_genetic_mutations):
        """The findings' reviewed gene concept ids, for the OMOP gene path."""
        items = [_concept_key(x.get('gene_concept_id')) for x in pi_genetic_mutations]
        return sorted({item for item in items if item})

    @staticmethod
    def mutation_legacy_gene_symbols(pi_genetic_mutations):
        """Symbols of findings with no mapped id; a mapped finding is excluded.

        Used only for the legacy symbol column: a finding that carries an id
        must not fall back to its own text, or a contradictory id could be
        rescued by the symbol it happens to mention.
        """
        symbols = set()
        for finding in pi_genetic_mutations:
            if _concept_key(finding.get('gene_concept_id')):
                continue
            gene = str(finding.get('gene') or '').strip()
            if gene:
                symbols.add(gene.lower())
        return sorted(symbols)

    @staticmethod
    def gene_match_context(pi_genetic_mutations):
        """The patient side of the gene rule, shared by search and matcher.

        Returns ``(keys, symbols)``: ``keys`` are the per-finding ids (or an
        unreviewed symbol), and ``symbols`` are the spellings a legacy trial
        can compare — only findings without an id, plus every reviewed spelling
        of the ids (including aliases) so a mapped finding never contributes
        its own contradictory text.
        """
        keys = set(GeneticMutations.mutation_gene_keys(pi_genetic_mutations))
        ids = {key for key in keys if key.isdigit()}
        symbols = set(GeneticMutations.mutation_legacy_gene_symbols(pi_genetic_mutations))
        symbols |= set(gene_symbols_for_concept_ids(ids))
        return keys, symbols

    @staticmethod
    def mutation_gene_keys(pi_genetic_mutations):
        """Per-finding identity keys: the mapped id, else a legacy symbol.

        The id wins over the finding's own symbol, so a contradictory mapped
        finding cannot be rescued by the text it happens to carry. A finding
        without an id is mapped through the reviewed catalog; an unreviewed
        symbol stays a symbol and can only match the legacy trial column.
        """
        keys = set()
        for finding in pi_genetic_mutations:
            concept_id = _concept_key(finding.get('gene_concept_id'))
            if concept_id:
                keys.add(concept_id)
                continue
            gene = str(finding.get('gene') or '').strip()
            if not gene:
                continue
            mapped = gene_concept_id(gene)
            keys.add(str(mapped) if mapped else gene.lower())
        return sorted(keys)

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
