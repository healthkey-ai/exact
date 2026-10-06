class TherapiesMapper:
    def data(self):
        return {
            "vrd": {
                "short_name": "VRd",
                "name": "VRd (Bortezomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Bortezomib", "Lenalidomide", "Dexamethasone"],
            },
            "dara_vrd": {
                "short_name": "Dara-VRd",
                "name": "Dara-VRd (Daratumumab, Bortezomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Daratumumab", "Bortezomib", "Lenalidomide", "Dexamethasone"],
            },
            "dara_rd": {
                "short_name": "Dara-Rd",
                "name": "Dara-Rd (Daratumumab, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Daratumumab", "Lenalidomide", "Dexamethasone"],
            },
            "vrd_lite": {
                "short_name": "VRd Lite",
                "name": "VRd Lite (Bortezomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Bortezomib", "Lenalidomide", "Dexamethasone"],
            },
            "krd": {
                "short_name": "KRd",
                "name": "KRd (Carfilzomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Carfilzomib", "Lenalidomide", "Dexamethasone"],
            },
            "isa_vrd": {
                "short_name": "Isa-VRd",
                "name": "Isa-VRd (Isatuximab, Bortezomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Isatuximab", "Bortezomib", "Lenalidomide", "Dexamethasone"],
            },
            "isa_krd": {
                "short_name": "Isa-KRd",
                "name": "Isa-KRd (Isatuximab, Carfilzomib, Lenalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Isatuximab", "Carfilzomib", "Lenalidomide", "Dexamethasone"],
            },
            "kpd": {
                "short_name": "KPd",
                "name": "KPd (Carfilzomib, Pomalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Carfilzomib", "Pomalidomide", "Dexamethasone"],
            },
            "epd": {
                "short_name": "EPd",
                "name": "EPd (Elotuzumab, Pomalidomide, and Dexamethasone)",
                "descr": "",
                "drugs": ["Elotuzumab", "Pomalidomide", "Dexamethasone"],
            },
            "svd": {
                "short_name": "SVd",
                "name": "SVd (Selinexor, Bortezomib, and Dexamethasone)",
                "descr": "",
                "drugs": ["Selinexor", "Bortezomib", "Dexamethasone"],
            },
            "cy_bor_d": {
                "short_name": "CyBorD",
                "name": "CyBorD (Cyclophosphamide, Bortezomib, and Dexamethasone)",
                "descr": "",
                "drugs": ["Cyclophosphamide", "Bortezomib", "Dexamethasone"],
            },
            "asct": {
                "name": "Autologous Stem Cell Transplant (ASCT)",
                "descr": "",
                "drugs": [],
            },
            "car_t": {
                "name": "CAR-T Cell Therapy",
                "descr": "",
                "drugs": [
                    "Idecabtagene vicleucel (Abecma)",
                    "Breyanzi (lisocabtagene maraleucel)",
                    "Tisagenlecleucel (Kymriah)",
                    "Tecartus (brexucabtagene autoleucel)",
                    "Axicabtagene Ciloleucel (Yescarta)",
                    "Ciltacabtagene autoleucel (Carvykti)"
                ],
            },
            "ba": {
                "name": "Bispecific Antibodies",
                "descr": "",
                # https://pmc.ncbi.nlm.nih.gov/articles/PMC10501874/ Table 1
                # NB! "Glofitamab" is not in the table
                "drugs": ["Catumaxomab", "Blinatumomab", "Mosunetuzumab", "Tebentafusp", "Teclistamab", "Amivantamab", "Cadonilimab", "Glofitamab"],
            },
            "selinexor": {
                "name": "Selinexor (Xpovio)",
                "descr": "May be introduced in combination with bortezomib and dexamethasone (SVd) for patients with prior exposure to other drug classes.",
                "drugs": [],
            },
            "ixazomib": {
                "name": "Ixazomib (Ninlaro)",
                "descr": "An oral proteasome inhibitor combined with lenalidomide and dexamethasone (IRd) for patients who need a less intensive regimen.",
                "drugs": [],
            },

            # FL
            "ww": {
                "name": "Watchful Waiting (Active Surveillance)",
                "descr": "",
                "drugs": [],
            },
            "rm": {
                "name": "Rituximab Monotherapy",
                "descr": "",
                "drugs": ["Rituximab"],
            },
            "r_chop": {
                "short_name": "R-CHOP",
                "name": "R-CHOP (Rituximab, Cyclophosphamide, Doxorubicin, Vincristine, Prednisone)",
                "descr": "",
                "drugs": ["Rituximab", "Cyclophosphamide", "Doxorubicin", "Vincristine", "Prednisone"],
            },
            "r_cvp": {
                "short_name": "R-CVP",
                "name": "R-CVP (Rituximab, Cyclophosphamide, Vincristine, Prednisone)",
                "descr": "",
                "drugs": ["Rituximab", "Cyclophosphamide", "Vincristine", "Prednisone"],
            },
            "br": {
                "short_name": "R-Bendamustine (BR)",
                "name": "R-Bendamustine (BR) (Bendamustine and Rituximab)",
                "descr": "A standard salvage regimen for relapsed FL, particularly effective in earlier relapses but may still be revisited in later rounds.",
                "drugs": ["Bendamustine", "Rituximab"],
            },
            "ifrt": {
                "name": "Involved-Field Radiotherapy (IFRT)",
                "descr": "",
                "drugs": [],
            },
            "obr": {
                "name": "Obinutuzumab-Based Regimens",
                "descr": "",
                "drugs": ["Obinutuzumab"],
            },
            "rm_rt": {
                "name": "Rituximab Maintenance or Re-Treatment",
                "descr": "",
                "drugs": ["Rituximab"],
            },
            "pi3k": {
                "short_name": "PI3K Inhibitors",
                "name": "PI3K Inhibitors (Copanlisib, Idelalisib, Umbralisib, Duvelisib and Alpelisib)",
                "descr": "",
                "drugs": ["Copanlisib", "Idelalisib", "Umbralisib", "Duvelisib", "Alpelisib"],
            },
            "tazemetostat": {
                "name": "Tazemetostat (Tazverik)",
                "descr": "An EZH2 inhibitor approved for FL with EZH2 mutations or in patients who have no other satisfactory options.",
                "drugs": ["Tazemetostat"],
            },
            "hdc_asct": {
                "name": "High-Dose Chemotherapy with Autologous Stem Cell Transplant (ASCT)",
                "descr": "",
                "drugs": [],
            },
            "lbr": {
                "short_name": "Lenalidomide-Based Regimens",
                "name": "Lenalidomide-Based Regimens (R2: Lenalidomide + Rituximab)",
                "descr": "",
                "drugs": ["Lenalidomide", "Rituximab"],
            },
            "ldr": {
                "name": "Low-Dose Radiotherapy",
                "descr": "",
                "drugs": [],
            },

            # added from 2025-02-25
            "daratumumab": {
                "name": "Daratumumab (Darzalex/Darzalex Faspro) Monotherapy",
                "descr": "Often combined with lenalidomide and dexamethasone (DRd) or bortezomib and dexamethasone (DVd) for patients who relapsed after first-line treatment.",
                "drugs": ["Daratumumab"],
            },
            "carfilzomib": {
                "name": "Carfilzomib (Kyprolis) Monotherapy",
                "descr": "Used in combination with dexamethasone (Kd) or lenalidomide and dexamethasone (KRd) for relapsed multiple myeloma.",
                "drugs": ["Carfilzomib"],
            },
            "lenalidomide": {
                "name": "Lenalidomide (Revlimid) Monotherapy",
                "descr": "Often reused in second-line regimens if the patient responded well in the first-line setting, combined with agents like daratumumab or carfilzomib.",
                "drugs": ["Lenalidomide"],
            },
            "pomalidomide": {
                "name": "Pomalidomide (Pomalyst) Monotherapy",
                "descr": "Used in combination with dexamethasone (Pd) or added to a proteasome inhibitor such as carfilzomib (KPd) for patients refractory to lenalidomide.",
                "drugs": ["Pomalidomide"],
            },
            "bortezomib": {
                "name": "Bortezomib (Velcade) Monotherapy",
                "descr": "Reused in second-line regimens, often in combination with daratumumab and dexamethasone (DVd) or other agents depending on the patient’s prior exposure.",
                "drugs": ["Bortezomib"],
            },
            "isatuximab": {
                "name": "Isatuximab (Sarclisa) Monotherapy",
                "descr": "An anti-CD38 monoclonal antibody combined with pomalidomide and dexamethasone (IPd) or carfilzomib and dexamethasone (IKd) for relapsed disease.",
                "drugs": ["Isatuximab"],
            },
            "elotuzumab": {
                "name": "Elotuzumab (Empliciti) Monotherapy",
                "descr": "A monoclonal antibody targeting SLAMF7, used in combination with lenalidomide and dexamethasone (ERd) or pomalidomide and dexamethasone (EPd).",
                "drugs": ["Elotuzumab"],
            },
            "cyclophosphamide": {
                "name": "Cyclophosphamide Monotherapy",
                "descr": "Sometimes included in second-line regimens (e.g., CyBorD: cyclophosphamide, bortezomib, and dexamethasone) for relapsed patients.",
                "drugs": ["Cyclophosphamide"],
            },
            "venetoclax": {
                "name": "Venetoclax Monotherapy",
                "descr": "Used off-label or in trials for patients with t(11;14) translocation, often in combination with bortezomib or dexamethasone.",
                "drugs": ["Venetoclax"],
            },
            "obinutuzumab": {
                "name": "Obinutuzumab (Gazyva) Monotherapy",
                "descr": "A CD20 monoclonal antibody used in combination with chemotherapy (e.g., bendamustine) for relapsed follicular lymphoma.",
                "drugs": ["Obinutuzumab"],
            },
            "rituximab": {
                "name": "Rituximab (Rituxan) Monotherapy",
                "descr": "A CD20 monoclonal antibody, often combined with chemotherapy or used as maintenance therapy in relapsed settings.",
                "drugs": ["Rituximab"],
            },
            "lr": {
                "name": "Lenalidomide (Revlimid) + Rituximab (R2)",
                "descr": "A chemotherapy-free regimen combining the immunomodulatory effects of lenalidomide with rituximab for relapsed or refractory FL.",
                "drugs": ["Lenalidomide", "Rituximab"],
            },
            "bendamustine": {
                "name": "Bendamustine Monotherapy",
                "descr": "A chemotherapy agent often paired with rituximab (BR) or obinutuzumab for patients with relapsed FL.",
                "drugs": ["Bendamustine"],
            },
            "polatuzumab_vedotin": {
                "name": "Polatuzumab Vedotin Monotherapy",
                "descr": "An antibody-drug conjugate targeting CD79b, used in combination with bendamustine and rituximab in some cases of refractory FL.",
                "drugs": ["Polatuzumab Vedotin"],
            },
            "idelalisib": {
                "name": "Idelalisib (Zydelig) Monotherapy",
                "descr": "A PI3K inhibitor approved for relapsed FL after at least two prior therapies.",
                "drugs": ["Idelalisib"],
            },
            "copanlisib": {
                "name": "Copanlisib (Aliqopa) Monotherapy",
                "descr": "An intravenous PI3K inhibitor used for relapsed FL after prior therapies.",
                "drugs": ["Copanlisib"],
            },
            "duvelisib": {
                "name": "Duvelisib (Copiktra) Monotherapy",
                "descr": "An oral PI3K inhibitor approved for relapsed or refractory FL after at least two prior lines of treatment.",
                "drugs": ["Duvelisib"],
            },
            "axicabtagene_ciloleucel": {
                "name": "Axicabtagene Ciloleucel (Yescarta) Monotherapy",
                "descr": "A CAR T-cell therapy approved for relapsed or refractory FL in patients who have failed at least two prior lines of therapy.",
                "drugs": ["Axicabtagene Ciloleucel (Yescarta)"],
            },
            "clinical_trials": {
                "name": "Clinical Trials",
                "descr": "Relapsed FL patients are encouraged to explore clinical trials for novel therapies, such as bispecific antibodies or next-generation targeted treatments.",
                "drugs": [],
            },
            "teclistamab": {
                "name": "Teclistamab (Tecvayli) Monotherapy",
                "descr": "A bispecific antibody targeting BCMA, approved for relapsed or refractory multiple myeloma.",
                "drugs": ["Teclistamab"],
            },
            "ide_cel": {
                "name": "Ide-cel (Abecma) Monotherapy",
                "descr": "A BCMA-targeted CAR T-cell therapy for heavily pretreated patients with RRMM.",
                "drugs": ["Ide-cel"],
            },
            "cilta_cel": {
                "name": "Cilta-cel (Carvykti) Monotherapy",
                "descr": "A BCMA-targeted CAR T-cell therapy demonstrating deep and durable responses in RRMM.",
                "drugs": ["Cilta-cel"],
            },
            "belantamab_mafodotin": {
                "name": "Belantamab Mafodotin (Blenrep) Monotherapy",
                "descr": "An antibody-drug conjugate targeting BCMA, delivering cytotoxic agents directly to myeloma cells.",
                "drugs": ["Belantamab Mafodotin (Blenrep)"],
            },
            "cyclophosphamide_or_melphalan": {
                "name": "Cyclophosphamide or Melphalan Monotherapy",
                "descr": "Alkylating agents used as part of salvage regimens in refractory settings.",
                "drugs": ["Cyclophosphamide", "Melphalan"],
            },
            "tisagenlecleucel": {
                "name": "Tisagenlecleucel Monotherapy",
                "descr": "Relapsed FL patients are encouraged to explore clinical trials for novel therapies, such as bispecific antibodies or next-generation targeted treatments.",
                "drugs": ["Tisagenlecleucel (Kymriah)"],
            },
            "agsct": {
                "name": "Allogeneic Stem Cell Transplant",
                "descr": "A curative option for select patients with relapsed or refractory FL who are eligible for intensive therapy.",
                "drugs": [],
            },
            "radiotherapy": {
                "name": "Radiotherapy",
                "descr": "Used in cases of localized relapse or palliation for symptomatic disease in heavily pretreated FL.",
                "drugs": ["Radiotherapy"],
            },

            # BC
            "lumpectomy": {
                "name": "Lumpectomy",
                "descr": "",
                "drugs": ["Lumpectomy"],
            },
            "mastectomy": {
                "name": "Mastectomy",
                "descr": "",
                "drugs": ["Mastectomy"],
            },
            "aromatase_inhibitors": {
                "name": "Aromatase Inhibitors",
                "descr": "",
                "drugs": ["Aromatase Inhibitor"],
            },
            "chemotherapy": {
                "name": "Chemotherapy",
                "descr": "",
                "drugs": ["Chemotherapy"],
            },
            "trastuzumab": {
                "name": "Trastuzumab",
                "descr": "",
                "drugs": ["Trastuzumab"],
            },
            "pertuzumab": {
                "name": "Pertuzumab",
                "descr": "",
                "drugs": ["Pertuzumab"],
            },
            "genomic_testing": {
                "name": "Genomic Testing",
                "descr": "",
                "drugs": ["Genomic Testing"],
            },
            "fulvestrant": {
                "name": "Fulvestrant",
                "descr": "",
                "drugs": ["Fulvestrant"],
            },
            "exemestane_everolimus": {
                "name": "Exemestane + Everolimus",
                "descr": "",
                "drugs": ["Exemestane", "Everolimus"],
            },
            "capisertib": {
                "name": "Capisertib",
                "descr": "",
                "drugs": ["Capisertib"],
            },
            "atezolizumab": {
                "name": "Atezolizumab",
                "descr": "",
                "drugs": ["Atezolizumab"],
            },
            "sacituzumab_govitecan": {
                "name": "Sacituzumab Govitecan",
                "descr": "",
                "drugs": ["Sacituzumab Govitecan"],
            },
            "platinum_based_chemotherapy": {
                "name": "Platinum-Based Chemotherapy",
                "descr": "",
                "drugs": ["Platinum-Based Chemotherapy"],
            },
            "parp_inhibitors": {
                "name": "PARP Inhibitors",
                "descr": "",
                "drugs": ["PARP Inhibitors"],
            },
            "other_chemotherapy": {
                "name": "Other Chemotherapy",
                "descr": "",
                "drugs": ["Other Chemotherapy"],
            },
            "surgery": {
                "name": "surgery",
                "descr": "",
                "drugs": ["surgery"],
            },
            "alpelisib_fulvestrant": {
                "name": "Alpelisib + Fulvestrant",
                "descr": "",
                "drugs": ["Alpelisib", "Fulvestrant"],
            },
            "capivasertib_fulvestrant": {
                "name": "Capivasertib + Fulvestrant",
                "descr": "",
                "drugs": ["Capivasertib", "Fulvestrant"],
            },
            "elacestrant": {
                "name": "Elacestrant",
                "descr": "",
                "drugs": ["Elacestrant"],
            },
            "tamoxifen": {
                "name": "Tamoxifen",
                "descr": "",
                "drugs": ["Tamoxifen"],
            },
            "megestrol_acetate": {
                "name": "Megestrol acetate",
                "descr": "",
                "drugs": ["Megestrol acetate"],
            },
            "capecitabine": {
                "name": "Capecitabine",
                "descr": "",
                "drugs": ["Capecitabine"],
            },
            "eribulin": {
                "name": "Eribulin",
                "descr": "",
                "drugs": ["Eribulin"],
            },
            "vinorelbine": {
                "name": "Vinorelbine",
                "descr": "",
                "drugs": ["Vinorelbine"],
            },
            "gemcitabine": {
                "name": "Gemcitabine",
                "descr": "",
                "drugs": ["Gemcitabine"],
            },
            "paclitaxel": {
                "name": "Paclitaxel",
                "descr": "",
                "drugs": ["Paclitaxel"],
            },
            "docetaxel": {
                "name": "Docetaxel",
                "descr": "",
                "drugs": ["Docetaxel"],
            },
            "trastuzumab_deruxtecan": {
                "name": "Trastuzumab Deruxtecan",
                "descr": "",
                "drugs": ["Trastuzumab Deruxtecan"],
            },
            "tucatinib_trastuzumab_capecitabine": {
                "name": "Tucatinib + Trastuzumab + Capecitabine",
                "descr": "",
                "drugs": ["Tucatinib", "Trastuzumab", "Capecitabine"],
            },
            "lapatinib": {
                "name": "Lapatinib",
                "descr": "",
                "drugs": ["Lapatinib"],
            },
            "neratinib": {
                "name": "Neratinib",
                "descr": "",
                "drugs": ["Neratinib"],
            },
            "margetuximab": {
                "name": "Margetuximab",
                "descr": "",
                "drugs": ["Margetuximab"],
            },
            "trastuzumab_emtansine": {
                "name": "Trastuzumab Emtansine",
                "descr": "",
                "drugs": ["Trastuzumab Emtansine"],
            },
            "atezolizumab_nab_paclitaxel": {
                "name": "Atezolizumab + Nab-Paclitaxel",
                "descr": "",
                "drugs": ["Atezolizumab", "Nab-Paclitaxel"],
            },
            "pembrolizumab_chemotherapy": {
                "name": "Pembrolizumab + Chemotherapy",
                "descr": "",
                "drugs": ["Pembrolizumab", "Chemotherapy"],
            },
            "olaparib": {
                "name": "Olaparib",
                "descr": "",
                "drugs": ["Olaparib"],
            },
            "talazoparib": {
                "name": "Talazoparib",
                "descr": "",
                "drugs": ["Talazoparib"],
            },
            "carboplatin": {
                "name": "Carboplatin",
                "descr": "",
                "drugs": ["Carboplatin"],
            },
            "cisplatin": {
                "name": "Cisplatin",
                "descr": "",
                "drugs": ["Cisplatin"],
            },
            "alpelisib": {
                "name": "Alpelisib",
                "descr": "",
                "drugs": ["Alpelisib"],
            },
            "capivasertib": {
                "name": "Capivasertib",
                "descr": "",
                "drugs": ["Capivasertib"],
            },
            "larotrectinib": {
                "name": "Larotrectinib",
                "descr": "",
                "drugs": ["Larotrectinib"],
            },
            "entrectinib": {
                "name": "Entrectinib",
                "descr": "",
                "drugs": ["Entrectinib"],
            },
            "immune_checkpoint_inhibitors": {
                "name": "Immune Checkpoint Inhibitors",
                "descr": "",
                "drugs": ["Immune Checkpoint Inhibitor"],
            },

            # added from 2025-08-21

            "systemic_corticosteroids_lt_5_mg_day": {
                "name": "systemic corticosteroids (e.g., prednisone) =< 5 mg/day",
                "descr": "",
                "drugs": [],
            },
            "systemic_corticosteroids_gt_5_mg_day": {
                "name": "systemic corticosteroids (e.g., prednisone) > 5 mg/day",
                "descr": "",
                "drugs": [],
            },
            "systemic_corticosteroids_gt_10_mg_day": {
                "name": "systemic corticosteroids (e.g., prednisone) > 10 mg/day",
                "descr": "",
                "drugs": [],
            },
            "systemic_corticosteroids_gt_20_mg_day": {
                "name": "systemic corticosteroids (e.g., prednisone) > 20 mg/day",
                "descr": "",
                "drugs": [],
            },
            "mineralocorticoids": {
                "name": "mineralocorticoids (e.g., fludrocortisone)",
                "descr": "",
                "drugs": [],
            },
            "inhaled_corticosteroids": {
                "name": "Inhaled corticosteroids",
                "descr": "",
                "drugs": [],
            },
            "topical_corticosteroids": {
                "name": "Topical corticosteroids",
                "descr": "",
                "drugs": [],
            },
            "intranasal_corticosteroids": {
                "name": "Intranasal corticosteroid",
                "descr": "",
                "drugs": [],
            },
            "immunosuppressant": {
                "name": "Immunosuppressant",
                "descr": "",
                "drugs": [],
            },
            "hormonal_therapy": {
                "name": "Hormonal therapy",
                "descr": "",
                "drugs": [],
            },
            "immunoglobulin_replacement_therapy": {
                "name": "Immunoglobulin replacement therapy",
                "descr": "",
                "drugs": [],
            },
            "antiviral": {
                "name": "antiviral",
                "descr": "",
                "drugs": [],
            },
            "warfarin_anticoagulant": {
                "name": "warfarin - anticoagulant",
                "descr": "",
                "drugs": [],
            },
            "heparin_anticoagulant": {
                "name": "heparin - anticoagulant",
                "descr": "",
                "drugs": [],
            },
            "aspirin_lt_81mg_daily": {
                "name": "Aspirin =< 81mg daily",
                "descr": "",
                "drugs": [],
            },
            "aspirin_gt_81mg_daily": {
                "name": "Aspirin > 81mg daily",
                "descr": "",
                "drugs": [],
            },
            "chronic_opioid_therapy": {
                "name": "Chronic opioid therapy",
                "descr": "",
                "drugs": [],
            },
            "nsaids": {
                "name": "NSAIDs",
                "descr": "",
                "drugs": [],
            },
            "hiv_antiretroviral_therapy": {
                "name": "HIV antiretroviral therapy",
                "descr": "",
                "drugs": [],
            },
            "herbal_supplements": {
                "name": "Herbal supplements (e.g., echinacea, ginseng, ginkgo biloba, high-dose turmeric)",
                "descr": "",
                "drugs": [],
            },

            "local_palliative_radiotherapy": {
                "name": "Local palliative radiotherapy",
                "descr": "",
                "drugs": ["Radiotherapy"],
            },
            "clarithromycin": {
                "name": "Clarithromycin (Biaxin) - antibiotic",
                "descr": "",
                "drugs": ["Clarithromycin"],
            },
            "erythromycin": {
                "name": "Erythromycin - antibiotic",
                "descr": "",
                "drugs": ["Erythromycin"],
            },
            "ciprofloxacin": {
                "name": "Ciprofloxacin (Cipro) - antibiotic",
                "descr": "",
                "drugs": ["Ciprofloxacin"],
            },
            "rifampin": {
                "name": "Rifampin (Rifadin, Rimactane) - antibiotic",
                "descr": "",
                "drugs": ["Rifampin"],
            },
            "rifabutin": {
                "name": "Rifabutin (Mycobutin) - antibiotic",
                "descr": "",
                "drugs": ["Rifabutin"],
            },
            "itraconazole": {
                "name": "Itraconazole (Sporanox) - antifungal",
                "descr": "",
                "drugs": ["Itraconazole"],
            },
            "ketoconazole": {
                "name": "Ketoconazole - antifungal",
                "descr": "",
                "drugs": ["Ketoconazole"],
            },
            "fluconazole": {
                "name": "Fluconazole (Diflucan) - antifungal",
                "descr": "",
                "drugs": ["Fluconazole"],
            },
            "voriconazole": {
                "name": "Voriconazole (Vfend) - antifungal",
                "descr": "",
                "drugs": ["Voriconazole"],
            },
            "posaconazole": {
                "name": "Posaconazole (Noxafil) - antifungal",
                "descr": "",
                "drugs": ["Posaconazole"],
            },
            "fluoxetine": {
                "name": "Fluoxetine (Prozac) - antidepressant",
                "descr": "",
                "drugs": ["Fluoxetine"],
            },
            "paroxetine": {
                "name": "Paroxetine (Paxil) - antidepressant",
                "descr": "",
                "drugs": ["Paroxetine"],
            },
            "fluvoxamine": {
                "name": "Fluvoxamine (Luvox) - antidepressant",
                "descr": "",
                "drugs": ["Fluvoxamine"],
            },
            "sertraline": {
                "name": "Sertraline (Zoloft) - antidepressant",
                "descr": "",
                "drugs": ["Sertraline"],
            },
            "bupropion": {
                "name": "Bupropion (Wellbutrin) - antidepressant",
                "descr": "",
                "drugs": ["Bupropion"],
            },
            "carbamazepine": {
                "name": "Carbamazepine (Tegretol) - anticonvulsant",
                "descr": "",
                "drugs": ["Carbamazepine"],
            },
            "phenytoin": {
                "name": "Phenytoin (Dilantin) - anticonvulsant",
                "descr": "",
                "drugs": ["Phenytoin"],
            },
            "phenobarbital": {
                "name": "Phenobarbital - anticonvulsant",
                "descr": "",
                "drugs": ["Phenobarbital"],
            },
            "topiramate": {
                "name": "Topiramate (Topamax) - anticonvulsant",
                "descr": "",
                "drugs": ["Topiramate"],
            },
            "st_john_s_wort": {
                "name": "St. John's Wort",
                "descr": "",
                "drugs": ["St. John's Wort"],
            },
            "grapefruit_juice": {
                "name": "Grapefruit juice",
                "descr": "",
                "drugs": ["Grapefruit juice"],
            },
            "pamidronate": {
                "name": "Pamidronate",
                "descr": "",
                "drugs": ["Pamidronate"],
            },
            "zoledronic_acid": {
                "name": "Zoledronic Acid",
                "descr": "",
                "drugs": ["Zoledronic Acid"],
            },
            "denosumab": {
                "name": "Denosumab (Xgeva)",
                "descr": "",
                "drugs": ["Denosumab"],
            },
            "g_csf": {
                "name": "Granulocyte-Colony Stimulating factor (G-CSFs)",
                "descr": "",
                "drugs": ["Growth Factor"],
            },
            "gm_csf": {
                "name": "Granulocyte–Macrophage Colony-Stimulating Factor (GM-CSF)",
                "descr": "",
                "drugs": ["Growth Factor"],
            },
            "esa": {
                "name": "Erythropoiesis-Stimulating Agent (ESA)",
                "descr": "",
                "drugs": ["Growth Factor"],
            },

            # Breast Cancer-Specific Supportive Therapies

            "her2_targeted_therapies": {
                "name": "HER2-targeted therapies",
                "descr": "",
                "drugs": [],
            },
            "lhrh_gnrh_agonists": {
                "name": "LHRH/GnRH agonists (e.g., goserelin, leuprolide, triptorelin, buserelin)",
                "descr": "",
                "drugs": [],
            },
            "palbociclib": {
                "name": "Palbociclib (Ibrance)",
                "descr": "",
                "drugs": ["Palbociclib"],
            },
            "ribociclib": {
                "name": "Ribociclib (Kisqali)",
                "descr": "",
                "drugs": ["Ribociclib"],
            },
            "abemaciclib": {
                "name": "Abemaciclib (Verzenio)",
                "descr": "",
                "drugs": ["Abemaciclib"],
            },
            "hrt": {
                "name": "HRT (Estrogen-containing medication)",
                "descr": "",
                "drugs": ["Estrogen"],
            },
            "contraceptives": {
                "name": "contraceptives (Estrogen-containing medication)",
                "descr": "",
                "drugs": ["Estrogen"],
            },
            "tamoxifen_maintenance": {
                "name": "Tamoxifen Maintenance",
                "descr": "",
                "drugs": ["Tamoxifen"],
            },
            "anastrozole_maintenance": {
                "name": "Anastrozole Maintenance",
                "descr": "",
                "drugs": ["Anastrozole"],
            },
            "letrozole_maintenance": {
                "name": "Letrozole Maintenance",
                "descr": "",
                "drugs": ["Letrozole"],
            },
            "exemestane_maintenance": {
                "name": "Exemestane Maintenance",
                "descr": "",
                "drugs": ["Exemestane"],
            },

            # Follicular Lymphoma-Specific Supportive Therapies

            "rituximab_maintenance_therapy": {
                "name": "Rituximab maintenance therapy",
                "descr": "",
                "drugs": ["Rituximab"],
            },
            "lenalidomide_maintenance": {
                "name": "Lenalidomide maintenance",
                "descr": "",
                "drugs": ["Lenalidomide"],
            },
            "ibritumomab_tiuxetan_radioimmunotherapy": {
                "name": "Ibritumomab tiuxetan radioimmunotherapy",
                "descr": "",
                "drugs": ["Ibritumomab tiuxetan"],
            },

            # Multiple Myeloma-Specific Supportive Therapies

            "ivig": {
                "name": "IVIG (intravenous immunoglobulin)",
                "descr": "",
                "drugs": [],
            },
            "plasmapheresis": {
                "name": "Plasmapheresis",
                "descr": "",
                "drugs": ["Plasmapheresis"],
            },
            "bortezomib_maintenance": {
                "name": "Bortezomib (maintenance)",
                "descr": "",
                "drugs": ["Bortezomib"],
            },
            "ixazomib_maintenance": {
                "name": "Ixazomib (maintenance)",
                "descr": "",
                "drugs": ["Ixazomib"],
            }
        }

    def category_mapping(self):
        return [
            {
                "code": "rituximab",
                "name": "Rituximab",
                "type": "Anti-CD20 Monoclonal Antibodies"
            },
            {
                "code": "obinutuzumab",
                "name": "Obinutuzumab",
                "type": "Anti-CD20 Monoclonal Antibodies"
            },
            {
                "code": "copanlisib",
                "name": "Copanlisib",
                "type": "PI3K Inhibitors"
            },
            {
                "code": "idelalisib",
                "name": "Idelalisib",
                "type": "PI3K Inhibitors"
            },
            {
                "code": "umbralisib",
                "name": "Umbralisib",
                "type": "PI3K Inhibitors"
            },
            {
                "code": "duvelisib",
                "name": "Duvelisib",
                "type": "PI3K Inhibitors"
            },
            {
                "code": "alpelisib",
                "name": "Alpelisib",
                "type": "PI3K Inhibitors"
            },
            {
                "code": "melphalan",
                "name": "Melphalan",
                "type": "Chemotherapy (Alkylating Agent)"
            },
            {
                "code": "cyclophosphamide",
                "name": "Cyclophosphamide",
                "type": "Chemotherapy (Alkylating Agent)"
            },
            {
                "code": "doxorubicin",
                "name": "Doxorubicin",
                "type": "Chemotherapy (Anthracycline)"
            },
            {
                "code": "liposomal_doxorubicin",
                "name": "Liposomal Doxorubicin",
                "type": "Chemotherapy (Anthracycline)"
            },
            {
                "code": "thalidomide",
                "name": "Thalidomide",
                "type": "Immunomodulatory Drug (IMiD)"
            },
            {
                "code": "lenalidomide",
                "name": "Lenalidomide (Revlimid)",
                "type": "Immunomodulatory Drug (IMiD)"
            },
            {
                "code": "pomalidomide",
                "name": "Pomalidomide (Pomalyst)",
                "type": "Immunomodulatory Drug (IMiD)"
            },
            {
                "code": "bortezomib",
                "name": "Bortezomib (Velcade)",
                "type": "Proteasome Inhibitor"
            },
            {
                "code": "carfilzomib",
                "name": "Carfilzomib (Kyprolis)",
                "type": "Proteasome Inhibitor"
            },
            {
                "code": "ixazomib",
                "name": "Ixazomib (Ninlaro)",
                "type": "Proteasome Inhibitor"
            },
            {
                "code": "daratumumab",
                "name": "Daratumumab (Darzalex)",
                "type": "Monoclonal Antibody (Anti-CD38)"
            },
            {
                "code": "isatuximab",
                "name": "Isatuximab (Sarclisa)",
                "type": "Monoclonal Antibody (Anti-CD38)"
            },
            {
                "code": "elotuzumab",
                "name": "Elotuzumab (Empliciti)",
                "type": "Monoclonal Antibody (Anti-SLAMF7)"
            },
            {
                "code": "teclistamab",
                "name": "Teclistamab (Tecvayli)",
                "type": "Bispecific Antibody"
            },
            {
                "code": "elranatamab",
                "name": "Elranatamab (Elrexfio)",
                "type": "Bispecific Antibody"
            },
            {
                "code": "talquetamab",
                "name": "Talquetamab (Talvey)",
                "type": "Bispecific Antibody"
            },
            {
                "code": "belantamab_mafodotin",
                "name": "Belantamab Mafodotin (Blenrep)",
                "type": "Antibody-Drug Conjugate (ADC)"
            },
            {
                "code": "dexamethasone",
                "name": "Dexamethasone",
                "type": "Corticosteroid"
            },
            {
                "code": "prednisone",
                "name": "Prednisone",
                "type": "Corticosteroid"
            },
            {
                "code": "idecabtagene_vicleucel",
                "name": "Idecabtagene vicleucel (Abecma)",
                "type": "CAR-T Cell Therapy"
            },
            {
                "code": "ciltacabtagene_autoleucel",
                "name": "Ciltacabtagene autoleucel (Carvykti)",
                "type": "CAR-T Cell Therapy"
            },
            {
                "code": "panobinostat",
                "name": "Panobinostat (Farydak)",
                "type": "HDAC Inhibitor"
            },
            {
                "code": "venetoclax",
                "name": "Venetoclax (Venclexta)",
                "type": "Targeted Therapy (BCL-2 Inhibitor)"
            },
            {
                "code": "selinexor",
                "name": "Selinexor (Xpovio)",
                "type": "Targeted Therapy (XPO1 Inhibitor)"
            },
            {
                "code": "asct",
                "name": "Autologous Stem Cell Transplant (ASCT)",
                "type": "Stem Cell Transplant"
            },
            {
                "code": "agsct",
                "name": "Allogeneic Stem Cell Transplant",
                "type": "Stem Cell Transplant"
            },
            {
                "code": "radiotherapy",
                "name": "Radiotherapy",
                "type": "Radiotherapy"
            },
            {
                "code": "zoledronic_acid",
                "name": "Zoledronic Acid",
                "type": "Supportive Therapy (Bisphosphonate)"
            },
            {
                "code": "pamidronate",
                "name": "Pamidronate",
                "type": "Supportive Therapy (Bisphosphonate)"
            },
            {
                "code": "denosumab",
                "name": "Denosumab (Xgeva)",
                "type": "Supportive Therapy (RANKL Inhibitor)"
            },
            {
                "code": "esas",
                "name": "Erythropoiesis-Stimulating Agents (ESAs)",
                "type": "Supportive Therapy"
            },
            {
                "code": "plasmapheresis",
                "name": "Plasmapheresis",
                "type": "Supportive Therapy"
            },
            {
                "code": "lenalidomide",
                "name": "Lenalidomide (Revlimid)",
                "type": "Treatment for High-Risk Smoldering Multiple Myeloma"
            },
            {
                "code": "daratumumab",
                "name": "Daratumumab (Darzalex)",
                "type": "Treatment for High-Risk Smoldering Multiple Myeloma"
            },
            {
                "code": "lumpectomy",
                "name": "Lumpectomy",
                "type": "Surgery"
            },
            {
                "code": "mastectomy",
                "name": "Mastectomy",
                "type": "Surgery"
            },
            {
                "code": "tamoxifen",
                "name": "Tamoxifen",
                "type": "Hormonal Therapy"
            },
            {
                "code": "aromatase_inhibitor",
                "name": "Aromatase Inhibitor",
                "type": "Hormonal Therapy"
            },
            {
                "code": "chemotherapy",
                "name": "Chemotherapy",
                "type": "Chemotherapy"
            },
            {
                "code": "trastuzumab",
                "name": "Trastuzumab",
                "type": "Monoclonal Antibody"
            },
            {
                "code": "pertuzumab",
                "name": "Pertuzumab",
                "type": "Monoclonal Antibody"
            },
            {
                "code": "genomic_testing",
                "name": "Genomic Testing",
                "type": "Diagnostic Tool"
            },
            {
                "code": "fulvestrant",
                "name": "Fulvestrant",
                "type": "Hormonal Therapy"
            },
            {
                "code": "exemestane",
                "name": "Exemestane",
                "type": "Hormonal Therapy"
            },
            {
                "code": "everolimus",
                "name": "Everolimus",
                "type": "mTOR Inhibitor"
            },
            {
                "code": "capisertib",
                "name": "Capisertib",
                "type": "Targeted Therapy"
            },
            {
                "code": "atezolizumab",
                "name": "Atezolizumab",
                "type": "Immunotherapy"
            },
            {
                "code": "sacituzumab_govitecan",
                "name": "Sacituzumab Govitecan",
                "type": "Antibody-Drug Conjugate"
            },
            {
                "code": "platinum_based_chemotherapy",
                "name": "Platinum-Based Chemotherapy",
                "type": "Chemotherapy"
            },
            {
                "code": "parp_inhibitors",
                "name": "PARP Inhibitors",
                "type": "Targeted Therapy"
            },
            {
                "code": "other_chemotherapy",
                "name": "Other Chemotherapy",
                "type": "Chemotherapy"
            },
            {
                "code": "surgery",
                "name": "Surgery",
                "type": "Surgery"
            },
            {
                "code": "elacestrant",
                "name": "Elacestrant",
                "type": "Hormonal Therapy"
            },
            {
                "code": "tamoxifen",
                "name": "Tamoxifen",
                "type": "Hormonal Therapy"
            },
            {
                "code": "megestrol_acetate",
                "name": "Megestrol acetate",
                "type": "Hormonal Therapy"
            },
            {
                "code": "capecitabine",
                "name": "Capecitabine",
                "type": "Chemotherapy"
            },
            {
                "code": "eribulin",
                "name": "Eribulin",
                "type": "Chemotherapy"
            },
            {
                "code": "vinorelbine",
                "name": "Vinorelbine",
                "type": "Chemotherapy"
            },
            {
                "code": "gemcitabine",
                "name": "Gemcitabine",
                "type": "Chemotherapy"
            },
            {
                "code": "paclitaxel",
                "name": "Paclitaxel",
                "type": "Chemotherapy"
            },
            {
                "code": "docetaxel",
                "name": "Docetaxel",
                "type": "Chemotherapy"
            },
            {
                "code": "trastuzumab_deruxtecan",
                "name": "Trastuzumab Deruxtecan",
                "type": "Antibody-Drug Conjugate"
            },
            {
                "code": "tucatinib",
                "name": "Tucatinib",
                "type": "Monoclonal Antibody"
            },
            {
                "code": "lapatinib",
                "name": "Lapatinib",
                "type": "Tyrosine Kinase Inhibitor"
            },
            {
                "code": "neratinib",
                "name": "Neratinib",
                "type": "Tyrosine Kinase Inhibitor"
            },
            {
                "code": "margetuximab",
                "name": "Margetuximab",
                "type": "Monoclonal Antibody"
            },
            {
                "code": "trastuzumab_emtansine",
                "name": "Trastuzumab Emtansine",
                "type": "Antibody-Drug Conjugate"
            },
            {
                "code": "sacituzumab_govitecan",
                "name": "Sacituzumab Govitecan",
                "type": "Antibody-Drug Conjugate"
            },
            {
                "code": "nab_paclitaxel",
                "name": "Nab-Paclitaxel",
                "type": "Chemotherapy"
            },
            {
                "code": "pembrolizumab",
                "name": "Pembrolizumab",
                "type": "Immunotherapy"
            },
            {
                "code": "olaparib",
                "name": "Olaparib",
                "type": "Targeted Therapy"
            },
            {
                "code": "talazoparib",
                "name": "Talazoparib",
                "type": "Targeted Therapy"
            },
            {
                "code": "carboplatin",
                "name": "Carboplatin",
                "type": "Chemotherapy"
            },
            {
                "code": "cisplatin",
                "name": "Cisplatin",
                "type": "Chemotherapy"
            },
            {
                "code": "alpelisib",
                "name": "Alpelisib",
                "type": "Targeted Therapy"
            },
            {
                "code": "capivasertib",
                "name": "Capivasertib",
                "type": "Targeted Therapy"
            },
            {
                "code": "larotrectinib",
                "name": "Larotrectinib",
                "type": "Targeted Therapy"
            },
            {
                "code": "entrectinib",
                "name": "Entrectinib",
                "type": "Targeted Therapy"
            },
            {
                "code": "immune_checkpoint_inhibitor",
                "name": "Immune Checkpoint Inhibitor",
                "type": "Immunotherapy"
            },
        ]

    def therapies(self):
        return {k: v["name"] for k, v in self.data().items()}

    def drugs(self):
        out = []
        for item in self.data().values():
            out = out + item["drugs"]
        out = list(set(out))
        out.sort()
        return out

    def first_line_mm(self):
        return ["vrd", "dara_vrd", "dara_rd", "vrd_lite", "cy_bor_d", "asct", "krd", "isa_vrd", "isa_krd"]

    def first_line_fl(self):
        return ["ww", "rm", "r_chop", "r_cvp", "br", "ifrt", "obr", "radiotherapy"]

    def first_line_bc(self):
        return ["lumpectomy", "mastectomy", "radiotherapy", "tamoxifen", "aromatase_inhibitors",
                "chemotherapy", "trastuzumab", "pertuzumab", "genomic_testing"]

    def second_line_mm(self):
        return [
            "daratumumab", "carfilzomib", "ixazomib", "lenalidomide", "pomalidomide", "bortezomib",
            "isatuximab", "elotuzumab", "cyclophosphamide", "selinexor", "venetoclax", "kpd"
        ]

    def second_line_fl(self):
        return [
            "obinutuzumab", "rituximab", "lr", "bendamustine", "polatuzumab_vedotin", "idelalisib",
            "copanlisib", "duvelisib", "tazemetostat", "axicabtagene_ciloleucel", "clinical_trials"
        ]

    def second_line_bc(self):
        return [
            "fulvestrant", "exemestane_everolimus", "capisertib", "atezolizumab", "sacituzumab_govitecan",
            "platinum_based_chemotherapy", "parp_inhibitors", "other_chemotherapy", "radiotherapy", "surgery"
        ]

    def later_therapy_mm(self):
        return [
            "daratumumab", "isatuximab", "carfilzomib", "ixazomib", "pomalidomide", "teclistamab",
            "ide_cel", "cilta_cel", "belantamab_mafodotin", "cyclophosphamide_or_melphalan", "selinexor",
            "venetoclax", "clinical_trials", "epd", "svd"
        ]

    def later_therapy_fl(self):
        return [
            "axicabtagene_ciloleucel", "tisagenlecleucel", "polatuzumab_vedotin", "idelalisib",
            "copanlisib", "duvelisib", "tazemetostat", "lr", "br", "obinutuzumab", "agsct",
            "radiotherapy", "clinical_trials"
        ]

    def later_therapy_bc(self):
        return [
            "fulvestrant", "exemestane_everolimus", "alpelisib_fulvestrant", "capivasertib_fulvestrant",
            "elacestrant", "tamoxifen", "megestrol_acetate", "capecitabine", "eribulin", "vinorelbine",
            "gemcitabine", "paclitaxel", "docetaxel", "trastuzumab_deruxtecan", "tucatinib_trastuzumab_capecitabine",
            "lapatinib", "neratinib", "margetuximab", "trastuzumab_emtansine", "sacituzumab_govitecan",
            "sacituzumab_govitecan", "atezolizumab_nab_paclitaxel", "pembrolizumab_chemotherapy", "olaparib",
            "talazoparib", "carboplatin", "cisplatin", "eribulin", "capecitabine", "gemcitabine", "vinorelbine",
            "olaparib", "talazoparib", "alpelisib", "capivasertib", "larotrectinib", "entrectinib", "immune_checkpoint_inhibitors",
        ]

    def supportive_all(self):
        return [
            "systemic_corticosteroids_lt_5_mg_day",
            "systemic_corticosteroids_gt_5_mg_day",
            "systemic_corticosteroids_gt_10_mg_day",
            "systemic_corticosteroids_gt_20_mg_day",
            "mineralocorticoids",
            "inhaled_corticosteroids",
            "topical_corticosteroids",
            "intranasal_corticosteroids",
            "immunosuppressant",
            "hormonal_therapy",
            "immunoglobulin_replacement_therapy",
            "antiviral",
            "warfarin_anticoagulant",
            "heparin_anticoagulant",
            "aspirin_lt_81mg_daily",
            "aspirin_gt_81mg_daily",
            "chronic_opioid_therapy",
            "nsaids",
            "hiv_antiretroviral_therapy",
            "herbal_supplements",
            "radiotherapy",
            "local_palliative_radiotherapy",
            "clarithromycin",
            "erythromycin",
            "ciprofloxacin",
            "rifampin",
            "rifabutin",
            "itraconazole",
            "ketoconazole",
            "fluconazole",
            "voriconazole",
            "posaconazole",
            "fluoxetine",
            "paroxetine",
            "fluvoxamine",
            "sertraline",
            "bupropion",
            "carbamazepine",
            "phenytoin",
            "phenobarbital",
            "topiramate",
            "st_john_s_wort",
            "grapefruit_juice",
            "pamidronate",
            "zoledronic_acid",
            "denosumab",
            "g_csf",
            "gm_csf",
            "esa"
        ]

    def supportive_mm(self):
        return [*self.supportive_all(), "ivig", "plasmapheresis", "lenalidomide_maintenance", "bortezomib_maintenance", "ixazomib_maintenance"]

    def supportive_fl(self):
        return [*self.supportive_all(), "rituximab_maintenance_therapy", "lenalidomide_maintenance", "ibritumomab_tiuxetan_radioimmunotherapy"]

    def supportive_bc(self):
        return [*self.supportive_all(), "her2_targeted_therapies", "lhrh_gnrh_agonists", "palbociclib", "ribociclib", "abemaciclib", "hrt", "contraceptives", "tamoxifen_maintenance", "anastrozole_maintenance", "letrozole_maintenance", "exemestane_maintenance"]


    def mm(self):
        return [
            "vrd", "dara_vrd", "dara_rd", "vrd_lite", "cy_bor_d", "asct", "car_t", "ba", "selinexor", "ixazomib"
        ]

    def fl(self):
        return [
            "ww", "rm", "r_chop", "r_cvp", "br", "ifrt", "obr", "r_chop", "r_cvp", "rm_rt", "pi3k", "tazemetostat",
            "hdc_asct", "lbr", "ldr"
        ]

    def breast_cancer(self):
        return []

    def cll(self):
        return []

    def first_round(self):
        return [
            "vrd", "dara_vrd", "dara_rd", "vrd_lite", "cy_bor_d", "asct", "ww", "rm", "r_chop", "r_cvp", "br",
            "ifrt", "obr"
        ]

    def later_round(self):
        return [
            "vrd", "cy_bor_d", "car_t", "ba", "selinexor", "ixazomib", "ww", "obr", "r_chop", "r_cvp", "rm_rt",
            "pi3k", "tazemetostat", "hdc_asct", "lbr", "ldr"
        ]

    def first_round_mm(self):
        return ["vrd", "dara_vrd", "dara_rd", "vrd_lite", "cy_bor_d", "asct"]

    def first_round_fl(self):
        return ["ww", "rm", "r_chop", "br", "r_cvp", "ifrt", "obr"]

    def later_round_mm(self):
        return ["vrd", "cy_bor_d", "car_t", "ba", "selinexor", "ixazomib"]

    def later_round_fl(self):
        return [
            "ww", "br", "r_chop", "r_cvp", "obr", "rm_rt", "pi3k", "tazemetostat", "car_t",
            "ba", "hdc_asct", "lbr", "ldr"
        ]

    def items(self, disease, kind):
        list1 = []
        list2 = []

        if disease.lower() == "mm" or disease.lower() == "multiple myeloma":
            list1 = self.mm()
        elif disease.lower() == "fl" or disease.lower() == "follicular lymphoma":
            list1 = self.fl()
        elif disease.lower() == "bc" or disease.lower() == "breast cancer":
            list1 = self.breast_cancer()
        elif disease.lower() == "cll" or disease.lower() == "chronic lymphocytic leukemia":
            list1 = self.cll()

        if kind == "first_round":
            list2 = self.first_round()
        elif kind == "later_round":
            list2 = self.later_round()

        ids = list(set(list1) & set(list2))
        ids.sort()

        therapies = self.data()

        return [{"code": code, "title": therapies[code]["name"]} for code in ids]
