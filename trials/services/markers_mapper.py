class MarkersMapper:
    # Canonical marker definitions (code -> name/description).  A marker can be
    # clinically relevant as cytogenic, molecular, or both; the two ordered code
    # lists below decide which catalog(s) it is seeded into.  Keeping a single
    # definition table avoids duplicating descriptions for shared markers.
    _MARKERS = {
        "del17p13": {
            "name": "Del(17p13) Deletion",
            "description": "Deletion of the TP53 gene on chromosome 17p, associated with poor prognosis and resistance to therapy.",
        },
        "t414": {
            "name": "t(4;14) Translocation",
            "description": "Translocation involving chromosomes 4 and 14, affecting the FGFR3 and MMSET genes, linked to high-risk disease.",
        },
        "t1114": {
            "name": "t(11;14) Translocation",
            "description": "Translocation between chromosomes 11 and 14, involving the CCND1 gene, commonly seen in plasma cell leukemia and considered standard or favorable risk.",
        },
        "t1416": {
            "name": "t(14;16) Translocation",
            "description": "Translocation between chromosomes 14 and 16, involving the MAF gene, associated with poor prognosis.",
        },
        "1q21Amplification": {
            "name": "1q21 Amplification",
            "description": "Gain of additional copies of chromosome 1q, associated with poor prognosis and treatment resistance.",
        },
        "hyperdiploidy": {
            "name": "Hyperdiploidy",
            "description": "Presence of multiple chromosomal gains, particularly of odd-numbered chromosomes (e.g., 3, 5, 7, 9, 11, 15, 19, 21), associated with better prognosis.",
        },
        "chromothripsis": {
            "name": "Chromothripsis",
            "description": "Extensive chromosomal rearrangements caused by a single catastrophic event, linked to aggressive disease and poor outcomes.",
        },
        "krasMutation": {
            "name": "KRAS Mutation",
            "description": "Mutation in the KRAS gene, often associated with disease progression and targeted by MAPK/ERK pathway inhibitors.",
        },
        "nrasMutation": {
            "name": "NRAS Mutation",
            "description": "Mutation in the NRAS gene, commonly seen in multiple myeloma and linked to disease progression.",
        },
        "brafMutation": {
            "name": "BRAF Mutation",
            "description": "Mutation in the BRAF gene, which may indicate responsiveness to targeted therapies like BRAF inhibitors.",
        },
        "mycRearrangements": {
            "name": "MYC Rearrangements",
            "description": "Structural abnormalities involving the MYC gene, associated with aggressive disease behavior.",
        },
        "tp53Mutation": {"name": "TP53 Mutation", "description": ""},
        "t414Fgfr3": {"name": "t(4;14) with FGFR3 activation", "description": ""},
        "1q21": {"name": "+1q (1q21 gain or amplification)", "description": ""},
        "ighRearrangements": {"name": "IGH Rearrangements", "description": ""},
        "cd38Expression": {"name": "CD38 Expression", "description": ""},
        "bcmaExpression": {"name": "BCMA Expression", "description": ""},
        "atmOrAtrMutations": {"name": "ATM or ATR Mutations", "description": ""},
        "notch1or2Mutations": {"name": "NOTCH1/NOTCH2 Mutations", "description": ""},
        "kmt2dMutation": {"name": "KMT2D Mutation", "description": ""},
        "nsd2Mutation": {"name": "NSD2 Mutation", "description": ""},
        "cdkn2aAlteration": {"name": "CDKN2A Alteration", "description": ""},
        "smarca4Mutation": {"name": "SMARCA4 Mutation", "description": ""},
        "ccnd1Alteration": {"name": "CCND1 Alteration", "description": ""},
        "bcl2Amplification": {"name": "BCL2 Amplification", "description": ""},
        "complexKaryotype": {"name": "Complex Karyotype", "description": ""},
        "complexKaryotypeExcludingT1114": {
            "name": "Complex Karyotype (≥3 abnormalities excluding t(11;14))",
            "description": "",
        },
        "fam46cMutation": {"name": "FAM46C Mutation", "description": ""},
        "dis3Mutation": {"name": "DIS3 Mutation", "description": ""},
        "xbp1Mutation": {"name": "XBP1 Mutation", "description": ""},
    }

    # Order is preserved when seeding so the generated option lists match the
    # legacy (single-table, ordered-by-id) behaviour.
    _CYTOGENIC_CODES = [
        "del17p13",
        "t414",
        "t1114",
        "t1416",
        "1q21Amplification",
        "hyperdiploidy",
        "chromothripsis",
        "krasMutation",
        "nrasMutation",
        "brafMutation",
        "mycRearrangements",
        "complexKaryotype",
        "complexKaryotypeExcludingT1114",
    ]

    _MOLECULAR_CODES = [
        "del17p13",
        "t1114",
        "t1416",
        "hyperdiploidy",
        "krasMutation",
        "nrasMutation",
        "brafMutation",
        "mycRearrangements",
        "tp53Mutation",
        "t414Fgfr3",
        "1q21",
        "ighRearrangements",
        "cd38Expression",
        "bcmaExpression",
        "atmOrAtrMutations",
        "notch1or2Mutations",
        "kmt2dMutation",
        "nsd2Mutation",
        "cdkn2aAlteration",
        "smarca4Mutation",
        "ccnd1Alteration",
        "bcl2Amplification",
        "complexKaryotype",
        "complexKaryotypeExcludingT1114",
        "fam46cMutation",
        "dis3Mutation",
        "xbp1Mutation",
    ]

    def cytogenic(self):
        return {code: self._MARKERS[code] for code in self._CYTOGENIC_CODES}

    def molecular(self):
        return {code: self._MARKERS[code] for code in self._MOLECULAR_CODES}
