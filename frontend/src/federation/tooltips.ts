// Tooltip descriptions for trial eligibility attribute fields.
// Keyed by the patient-side field name (ufield) that EXACT returns in
// trialEligibilityAttributes. Source: CancerBot lang/resources.en.json.
export const FIELD_TOOLTIPS: Record<string, string> = {
  "absoluteLymphocyteCount": `Absolute lymphocyte count (ALC) in peripheral blood:

CLL is diagnosed when ALC ≥5,000/μL (5 × 10⁹/L) with clonal B-lymphocytes for ≥3 months.
Monoclonal B-lymphocytosis (MBL) is diagnosed when ALC <5,000/μL.
Rising ALC over time may indicate disease progression.
Very high ALC (>100,000/μL) may cause hyperviscosity symptoms.`,
  "absoluteNeutrophileCount": `What is the absolute neutrophil white blood cell count?`,
  "albumin": `What is Albumin protein level?`,
  "autoimmuneCytopeniasRefractoryToSteroids": `Autoimmune cytopenias in CLL are immune-mediated destruction of blood cells:

Autoimmune Hemolytic Anemia (AIHA): Immune destruction of red blood cells causing anemia.
Immune Thrombocytopenia (ITP): Immune destruction of platelets causing low platelet count.
Refractory to Steroids: Cytopenias that do not adequately respond to corticosteroid therapy.
Presence indicates more aggressive disease and is an indication for treatment according to iwCLL criteria.`,
  "bcl2InhibitorRefractory": `BCL-2 (B-cell lymphoma 2) inhibitor refractory disease:

Disease progression occurring while on BCL-2 inhibitor therapy (e.g., venetoclax).
Or progression within 12 months of stopping BCL-2 inhibitor.
Indicates highly resistant disease with limited treatment options.
May require novel therapies, combination approaches, or cellular therapy (CAR-T).
Associated with very poor prognosis.`,
  "binetStage": `Binet staging system for Chronic Lymphocytic Leukemia (CLL):

Stage A:
Fewer than 3 lymphoid areas involved (lymph nodes, spleen, or liver).
No anemia (hemoglobin ≥10 g/dL) or thrombocytopenia (platelet count ≥100,000/μL).
Prognosis: Good, with median survival often >10 years.

Stage B:
3 or more lymphoid areas involved.
No anemia or thrombocytopenia.
Prognosis: Intermediate, median survival ~5-8 years.

Stage C:
Anemia (hemoglobin <10 g/dL) and/or thrombocytopenia (platelet count <100,000/μL).
Any number of lymphoid areas involved.
Prognosis: Poorest among the stages, median survival ~2-4 years without treatment.`,
  "biopsyGrade": `Grade 1:
Low grade DCIS or (score 3, 4, or 5 in Invasive Breast Cancer)
Well differentiated (slow growth, good prognosis).
Grade 2:
Intermediate grade DCIS or (score 6, 7 in Invasive Breast Cancer)
Moderately differentiated (intermediate prognosis).
Grade 3:
High grade DCIS or (score 8, 9 in Invasive Breast Cancer)
Poorly differentiated (fast growth, worse prognosis).`,
  "bloodPressure": `Enter systolic (SBP) and diastolic (DBP) blood pressure`,
  "boneImagingResult": `Yes indicates finding of bone abnormalities. No indicates normal`,
  "boneLesions": `What is the bone lesions level`,
  "boneMarrowInvolvement": `Bone marrow involvement in CLL:

CLL cells infiltrate the bone marrow, often extensively.
Diagnosed by bone marrow biopsy showing ≥30% lymphocytes (by iwCLL criteria for CLL diagnosis).
Extensive involvement can cause cytopenias (anemia, thrombocytopenia, neutropenia).
Patterns include nodular, interstitial, or diffuse infiltration.`,
  "boneOnlyMetastasisStatus": `Select ‘Yes’ if metastases are present only in the bones and no other organs; otherwise, ‘No’.`,
  "btkInhibitorRefractory": `BTK (Bruton's tyrosine kinase) inhibitor refractory disease:

Disease progression occurring while on BTK inhibitor therapy (e.g., ibrutinib, acalabrutinib, zanubrutinib).
Or progression within 12 months of stopping BTK inhibitor.
Indicates aggressive disease with poor prognosis.
May be due to acquired resistance mutations (e.g., BTK C481S, PLCG2).
Requires alternative therapies such as BCL-2 inhibitors, PI3K inhibitors, or cellular therapy.`,
  "bulkyDiseaseCriteria": `Bulky disease criteria in MCL based on size thresholds across lesion, nodal, and spleen measurements:

Bulky lesion ≥5 cm, ≥7.5 cm, or ≥10 cm: Largest measurable lesion exceeds threshold.
Bulky lymph node ≥5 cm, ≥7.5 cm, or ≥10 cm: Largest lymph node exceeds threshold.
Spleen size >13 cm, >15 cm, >20 cm, or ≥20 cm: Splenomegaly meeting specific threshold.
Derived automatically from lesion size, lymph node size, and spleen size measurements.`,
  "caregiverAvailabilityStatus": `Do you have an available caregiver?`,
  "clonalBLymphocyteCount": `Absolute count of clonal B-lymphocytes:

Represents the number of malignant CLL cells in peripheral blood.
Used to monitor disease burden and treatment response.
High counts may indicate active or progressive disease.
Counts may rise during treatment with certain targeted therapies (lymphocytosis).`,
  "clonalBoneMarrowBLymphocytes": `Percentage of clonal B-lymphocytes in bone marrow:

CLL diagnosis requires ≥30% monoclonal B-lymphocytes in bone marrow (by iwCLL criteria).
Higher percentages indicate greater bone marrow involvement.
Extensive involvement (>80%) may be associated with cytopenias.
Monitored to assess disease burden and treatment response.`,
  "clonalPlasmaCells": `What percentage of clonal plasma cells were found in your bone marrow?`,
  "concomitantMedications": `List of concomitant medications`,
  "consentCapability": `Do you have the ability to consent to the treatment in the trial?`,
  "contraceptiveUse": `Are you using contraceptives?`,
  "creatinineClearanceRate": `What is the creatine clearance rate in mL/minute`,
  "cytogenicMarkers": `Del(17p13):
Deletion of the TP53 gene on chromosome 17p, associated with poor prognosis and resistance to therapy.
t(4;14):
Translocation involving chromosomes 4 and 14, affecting the FGFR3 and MMSET genes, linked to high-risk disease.
t(11;14):
Translocation between chromosomes 11 and 14, involving the CCND1 gene, commonly seen in plasma cell leukemia and considered standard or favorable risk.
t(14;16):
Translocation between chromosomes 14 and 16, involving the MAF gene, associated with poor prognosis.
1q21 Amplification:
Gain of additional copies of chromosome 1q, associated with poor prognosis and treatment resistance.
Hyperdiploidy:
Presence of multiple chromosomal gains, particularly of odd-numbered chromosomes (e.g., 3, 5, 7, 9, 11, 15, 19, 21), associated with better prognosis.
Chromothripsis:
Extensive chromosomal rearrangements caused by a single catastrophic event, linked to aggressive disease and poor outcomes.
KRAS Mutation:
Mutation in the KRAS gene, often associated with disease progression and targeted by MAPK/ERK pathway inhibitors.
NRAS Mutation:
Mutation in the NRAS gene, commonly seen in multiple myeloma and linked to disease progression.
BRAF Mutation:
Mutation in the BRAF gene, which may indicate responsiveness to targeted therapies like BRAF inhibitors.
MYC Rearrangements:
Structural abnormalities involving the MYC gene, associated with aggressive disease behavior.`,
  "disease": `We currently just support MM, FL and BC trials`,
  "diseaseActivity": `Current disease activity status in CLL:

Active: Progressive disease with worsening symptoms, increasing lymphocyte count, or organ involvement requiring treatment.
Smoldering: Asymptomatic with stable or slowly increasing disease burden, not requiring immediate treatment.
Stable: Disease is controlled on current therapy or observation.
Progressive: Disease is advancing despite treatment or observation, meeting iwCLL criteria for progression.`,
  "diseaseBehavior": `The overall clinical behavior of Mantle Cell Lymphoma:

Indolent: Slow-growing disease, often non-nodal leukemic presentation. May not require immediate treatment. Associated with longer progression-free survival.
Aggressive: Rapidly progressive disease requiring prompt systemic therapy. Associated with blastoid or pleomorphic morphology and higher proliferation index.`,
  "diseaseSubtype": `The clinical subtype of Mantle Cell Lymphoma based on presentation pattern:

In situ MCN (ISMCN): Earliest form, confined within mantle zones of lymph nodes. Typically an incidental finding, often indolent.
Conventional nodal MCL (cMCL): Involves lymph nodes with typical nodal architecture effacement. Most common presentation.
Leukemic non-nodal MCL (nnMCL): Circulates in blood with spleen and bone marrow involvement but minimal lymph node disease. Often SOX11-negative, generally indolent but can transform.`,
  "distance": `Fill this in if you want to search for trials by distance instead of state or region`,
  "distantMetastasisStage": `Tells whether cancer has spread to distant organs (like bone, liver, or lung):
M0 : No distant metastasis
M0(i+) : means there is no sign of cancer spread to a different part of the body on physical examination or scans. But cancer cells are present in the blood, bone marrow, or lymph nodes far away from the breast. The cells are found by laboratory tests.
M1 : Distant metastasis present, means the cancer has spread to another part of the body. This is seen on scans, felt by the doctor, or has been confirmed by looking at a tissue sample removed during a biopsy or surgery.`,
  "ecogPerformanceStatus": `0: Fully active, able to carry on all pre-disease activities without restriction.
1: Restricted in physically strenuous activity but ambulatory and able to carry out work of a light or sedentary nature (e.g., light housework, office work).
2: Ambulatory and capable of all self-care but unable to carry out any work activities; up and about more than 50% of waking hours.
3: Capable of only limited self-care; confined to bed or chair more than 50% of waking hours.
4: Completely disabled; cannot carry on any self-care; totally confined to bed or chair.`,
  "ejectionFraction": `Percentage of blood pumped out ventricle`,
  "estimatedGlomerularFiltrationRate": `What is the estimated glomerular filtration rate in mL/min/1.73 m²`,
  "estrogenReceptorStatus": `Select your Estrogen receptor (ER) status, which indicates whether breast cancer cells have receptors for estrogen:
ER–: Tumor cells do not express estrogen receptors. (<1 % of cells express ER)
ER+: Tumor cells express estrogen receptors. (10% or more).
ER+ with low expression: Small proportion of tumor cells express ER (usually 1–9 %).
ER+ with high expression: Large proportion of tumor cells express ER.`,
  "ethnicity": `We use your ethnicity to compute normal levels for creatinine and other diagnostics`,
  "extranodalSites": `Sites outside lymph nodes involved by Mantle Cell Lymphoma, assessed via imaging or biopsy:

None: No extranodal involvement detected.
Bone marrow: MCL cells present in the bone marrow; common in advanced disease.
GI tract: Gastrointestinal involvement, often presenting as polyps (lymphomatous polyposis).
Spleen: Splenic involvement; may cause splenomegaly.
Liver: Hepatic involvement; often indicates advanced disease.
Waldeyer ring: Involvement of tonsillar/pharyngeal lymphoid tissue.
CNS: Central nervous system involvement; uncommon but associated with aggressive disease.
Other: Any other extranodal site.`,
  "firstLineDate": `When was the initial therapy you had for your disease?`,
  "firstLineTherapy": `What was the initial therapy you had for your disease?`,
  "flipiScoreOptions": `Age: Greater than 60 years.
Ann Arbor Stage: Stage III or IV disease.
Hemoglobin Level: Less than 12 g/dL.
Number of Nodal Areas Involved: More than four.
Serum Lactate Dehydrogenase (LDH) Level: Above the normal range.`,
  "gelfCriteriaStatus": `Nodal/Extranodal Mass ≥7 cm:
Any single tumor mass 7 cm or larger.
Multiple Nodal Sites >3 cm:
At least three nodes, each larger than 3 cm.
Systemic 'B' Symptoms:
Fever, night sweats, or weight loss.
Large Splenomegaly:
Spleen extends below the umbilical line (≥16 cm).
Pleural Effusion/Ascites:
Presence of fluid around the lungs or abdomen.
Organ Compression:
Tumors causing significant organ compression or dysfunction.
Bone Marrow Involvement:
Bone marrow involvement leading to cytopenias, such as hemoglobin <10 g/dL or platelet count <100 × 10⁹/L.`,
  "gender": `Enter your biological sex`,
  "gene": `Select the gene where your mutation was detected.
Common breast cancer-related genes include: BRCA1, BRCA2, TP53, PIK3CA, and ESR1.`,
  "height": `Enter your height`,
  "hemoglobinLevel": `What is the blood hemoglobin level`,
  "hepatomegaly": `Hepatomegaly means enlarged liver:

In CLL, the liver may be infiltrated with leukemia cells causing enlargement.
Clinically defined as liver palpable >2 cm below the right costal margin.
May indicate more advanced or active disease.
Can contribute to cytopenias and other complications.`,
  "her2Status": `Enter your HER2 status, which describes whether breast cancer cells have high levels of the HER2 protein (by IHC testing) or gene amplification (by ISH):
HER2+: Tumor shows HER2 protein overexpression (IHC 3+) or gene amplification (ISH+).
HER2−: Tumor shows no HER2 overexpression (IHC 0 or 1+, ISH−).
HER2-low: Tumor with low HER2 expression (IHC 1+ or 2+ with ISH−).`,
  "highRiskMclCriteria": `High-risk features in Mantle Cell Lymphoma derived from molecular, cytogenetic, morphological, and clinical findings:

Molecular: TP53 mutation, KMT2D mutation, NSD2 mutation, NOTCH1/NOTCH2 mutation, CDKN2A alteration, SMARCA4 mutation, CCND1 alteration, BCL2 amplification.
Cytogenetic: del(17p), complex karyotype (≥3 abnormalities), MYC rearrangement.
Protein expression: p53 IHC ≥50%.
Morphology: Blastoid or Pleomorphic variant.
Proliferation: Ki67 >30%, ≥30%, >50%, or ≥50%.
MIPI score: High MIPI (≥6.2) or combined MIPI-c High/High-Intermediate.
Bulky disease: Lesion, node, or spleen exceeding defined size thresholds.
Lymphocytosis: ALC ≥50,000 cells/µL.
Derived automatically from available patient data.`,
  "histologicType": `Breast Cancer Histologic Types:
IDC: Malignant tumor originating in mammary ducts, invading surrounding tissue.
ILC: Malignant tumor arising from lobules, with discohesive cells and diffuse infiltration.
DCIS: Non-invasive malignant cells confined to the ductal system.
LCIS: Non-invasive lesion of the lobules; marker of increased breast cancer risk rather than a true malignancy.
Inflammatory Breast Cancer: Aggressive carcinoma with dermal lymphatic invasion, presenting clinically with erythema and edema (‘peau d’orange’).
Paget’s Disease of the Breast: Malignant proliferation of nipple-areolar epithelium, often linked to DCIS or invasive carcinoma.
Metaplastic Breast Cancer: Heterogeneous carcinoma with epithelial cells showing squamous, spindle, or mesenchymal differentiation.`,
  "hrdStatus": `Select your HRD (Homologous Recombination Deficiency) status:
HRD+: Tumor shows deficiency in homologous recombination DNA repair, which may make it more sensitive to certain therapies like PARP inhibitors.
HRD−: Tumor does not show homologous recombination deficiency.`,
  "interpretation": `Select how the variant is classified based on current evidence:
Pathogenic / Likely pathogenic: disease-causing
Variant of Uncertain Significance (VUS): unclear impact
Likely benign / Benign: not disease-causing
No mutation detected: no variant identified.`,
  "kappaFLC": `What is Kappa FLC?`,
  "karnofskyPerformanceScore": `100: Normal, no complaints, no evidence of disease.
90: Able to carry on normal activity; minor signs or symptoms of disease.
80: Normal activity with effort; some signs or symptoms of disease.
70: Cares for self, unable to carry on normal activity or do active work.
60: Requires occasional assistance but can care for most needs.
50: Requires considerable assistance and frequent medical care.
40: Disabled; requires special care and assistance.
30: Severely disabled; hospitalization is indicated, though death not imminent.
20: Very sick; hospitalization necessary; active supportive treatment needed.
10: Moribund; fatal processes progressing rapidly.`,
  "ki67ProliferationIndex": `Ki-67 is a protein found only in cells that are actively dividing. The Ki-67 proliferation index measures how fast cancer cells in your tumor are dividing. It’s also called the Ki-67 score, and can help predict how your breast cancer may respond to treatments such as chemotherapy.
How it’s measured: It is determined by staining tumor tissue with an antibody that detects the Ki-67 protein, and the result is reported as the percentage of tumor cell nuclei that test positive.
Why it matters: A high index means many cells are dividing quickly, so the cancer may grow and spread more aggressively. Although cut-offs aren’t universally agreed upon, a level over 30% is often considered high.`,
  "lactateDehydrogenaseLevel": `What is the lactate dehydrogenase (LDH) level?`,
  "lambdaFLC": `What is Lambda FLC?`,
  "languages": `Languages you know (English, Spanish, or other).`,
  "languagesSkills": `Specify whether you can speak/understand or read/write the language.`,
  "largestLesionSize": `Size of the largest measurable lesion in centimeters:

Measured in longest diameter by CT or PET-CT imaging.
Used to assess disease burden and monitor response to treatment.
Lesions ≥10 cm are considered bulky disease.
Rapid increase in lesion size may indicate disease progression.`,
  "largestLymphNodeSize": `Size of the largest lymph node in centimeters:

Measured in longest diameter by CT scan or physical examination.
Used to monitor disease progression and response to treatment.
Rapid increase in lymph node size may indicate disease progression or Richter transformation.
Lymph nodes ≥10 cm are sometimes called 'bulky disease'.`,
  "lastTreatment": `What was the date of your last therapy?`,
  "lastUpdate": `How many years since the last update posted for the study?`,
  "laterDate": `When did you receive third or later rounds?`,
  "laterTherapy": `What therapies did you receive on third or later rounds?`,
  "liverEnzymeLevelsAlp": `What are ALP liver enzyme levels?`,
  "liverEnzymeLevelsAlt": `What are ALT liver enzyme levels?`,
  "liverEnzymeLevelsAst": `What are AST liver enzyme levels?`,
  "location": `Enter country that you live in and your zip code in US or postal code elsewhere. This is used to do distance searches for trials.`,
  "lymphadenopathy": `Lymphadenopathy refers to enlarged lymph nodes:

In CLL, lymph nodes are commonly enlarged due to infiltration by leukemia cells.
Measured by physical examination or imaging (CT/PET scan).
Lymph nodes ≥1.5 cm in longest diameter are considered significant.
Extent and progression of lymphadenopathy helps determine disease stage and need for treatment.`,
  "lymphocyteDoublingTime": `Lymphocyte doubling time is the time in months for the absolute lymphocyte count to double:

Less than 6 months indicates rapid disease progression and is an iwCLL criterion for active disease requiring treatment.
6-12 months may indicate active disease.
Greater than 12 months suggests stable or slowly progressive disease.
Calculated from serial blood counts over time.`,
  "measurableDiseaseByRecistStatus": `Answer "Yes" if you have Any one of the following:
- Tumor ≥ 1 cm on CT/MRI scan
- Enlarged lymph node ≥ 1.5 cm on CT/MRI scan
- Tumor ≥ 2 cm on chest X-ray
- Tumor ≥ 1 cm on physical exam (palpable lump)`,
  "measurableDiseaseImwg": `"Yes" means you have any of the following:
- Serum M-protein ≥ 0.5 g/dL
- Urine M-protein ≥ 200 mg/24h
- Serum free light chain (FLC) ≥ 100 mg/L with abnormal κ/λ ratio`,
  "measurableDiseaseIwcll": `Measurable disease according to International Workshop on Chronic Lymphocytic Leukemia (iwCLL) criteria:

Answer "Yes" if you have any of the following:
- Absolute lymphocyte count ≥5,000/μL in peripheral blood
- Lymphadenopathy (lymph nodes ≥1.5 cm in longest diameter by CT scan)
- Splenomegaly (spleen >13 cm in length by CT scan)
- Hepatomegaly (liver enlarged below costal margin)
- Bone marrow involvement with ≥30% lymphocytes`,
  "meetsCRAB": `Does blood test meets CRAB?
C: Elevated blood calcium levels.
R: Kidney damage, often indicated by high creatinine levels in the blood.
A: Anemia, which is a reduction in the number of red blood cells.
B: Bone lesions, which can be seen on X-rays or other imaging scans.`,
  "meetsLugano": `Does the patient meet Lugano criteria for response assessment in lymphoma?

Lugano criteria are used to evaluate treatment response in lymphomas including MCL and FL, based on PET/CT and CT imaging findings.`,
  "meetsSLIM": `Does blood test meets SLIM?`,
  "menopausalStatus": `Enter your menopausal status:
Pre-menopausal: Ongoing regular or irregular menstrual cycles.
Post-menopausal: No menstrual periods for 12 consecutive months, or confirmed menopause by FSH/LH/estradiol levels. This also includes menopause caused by surgery (removal of ovaries), chemotherapy, or hormone therapy.`,
  "metastaticStatus": `‘Yes’ only if Stage IV (metastatic); otherwise ‘No’.`,
  "mipiCRisk": `The combined biological MIPI score (MIPI-c), which integrates MIPI risk category with Ki-67 proliferation index to refine prognosis in MCL.

Low: MIPI Low + Ki-67 < 30%
Low-Intermediate: MIPI Low + Ki-67 ≥ 30%, or MIPI Intermediate + Ki-67 < 30%
High-Intermediate: MIPI Intermediate + Ki-67 ≥ 30%, or MIPI High + Ki-67 < 30%
High: MIPI High + Ki-67 ≥ 30%

Reference: Hoster et al., Blood 2008.`,
  "mipiRisk": `The MIPI (Mantle Cell Lymphoma International Prognostic Index) risk category, computed from age, ECOG performance status, LDH level, and WBC count.

Low risk: MIPI score < 5.7
Intermediate risk: 5.7 ≤ MIPI < 6.2
High risk: MIPI ≥ 6.2

Reference: Hoster et al., Blood 2008.`,
  "molecularMarkers": `t(11;14):
Type: Inclusion
Description: Associated with BCL2 overexpression; often included in trials for BCL2 inhibitors.
TP53 mutation or del(17p):
Type: Inclusion/Exclusion
Description: Associated with high-risk disease; may be included in high-risk focused trials or excluded if treatment has limited efficacy in high-risk profiles.
BRAF, KRAS, or NRAS mutations:
Type: Inclusion/Exclusion
Description: Common mutations influencing disease course; included for targeted therapy trials, may be excluded if treatment does not address these pathways.
t(4;14) with FGFR3 activation:
Type: Inclusion/Exclusion
Description: High-risk translocation included in FGFR3-targeted trials but may be excluded in standard-risk trials.
+1q (1q21 gain or amplification):
Type: Inclusion
Description: Associated with aggressive disease; often included for high-risk or novel therapeutic trials.
MYC rearrangements:
Type: Exclusion
Description: Linked to aggressive disease; may be excluded from trials not addressing MYC pathway.
IGH translocations:
Type: Inclusion
Description: Includes common translocations like t(4;14), t(14;16), t(11;14); may be included in trials targeting these specific pathways.
CD38 expression:
Type: Inclusion
Description: High CD38 expression allows for inclusion in trials for CD38-targeted therapies, such as monoclonal antibodies.
BCMA expression:
Type: Inclusion/Exclusion
Description: High BCMA expression leads to inclusion in BCMA-targeted therapy trials, may be excluded from non-BCMA trials.
ATM or ATR mutations:
Type: Inclusion/Exclusion
Description: Mutations affecting DNA repair pathways; included in DNA repair-targeted trials, excluded if trial does not target this mechanism.
NOTCH1/NOTCH2 mutations:
Type: Inclusion
Description: Included in trials focused on NOTCH pathway inhibition.
Complex karyotype:
Type: Exclusion
Description: Associated with high genomic instability; may be excluded in standard-risk focused trials.`,
  "monoclonalProteinSerum": `What is the blood serum monoclonal protein level?`,
  "monoclonalProteinUrine": `What is the urine monoclonal protein level?`,
  "morphologicVariant": `Morphologic variant of Mantle Cell Lymphoma based on histological appearance:

Classic: Monotonous small to medium lymphocytes with irregular nuclei; most common variant.
Blastoid: Cells resembling lymphoblasts with dispersed chromatin; more aggressive behavior.
Pleomorphic: Large irregular cells with abundant cytoplasm; associated with poor prognosis.
Variant morphology correlates with disease aggressiveness and may influence treatment choice.`,
  "noActiveInfectionStatus": `Yes if there are other active infections. Otherwise No.`,
  "noConcomitantMedicationStatus": `Are you taking any concomitant medications?`,
  "noGeographicExposureRisk": `Have you had any geograhic risk exposure to infection or toxins?`,
  "noHepatitisBStatus": `Do you have Hepatitis B?`,
  "noHepatitisCStatus": `Do you have Hepatitis C?`,
  "noHivStatus": `Are HIV positive?`,
  "noMentalHealthDisorderStatus": `Do you have any mental health disorders?`,
  "noOtherActiveMalignancies": `Yes if there are other active malignancies. Otherwise No.`,
  "noPregnancyOrLactationStatus": `Are you pregnant or lactating?`,
  "noSubstanceUseStatus": `Are you using any non-prescription drugs?`,
  "noTobaccoUseStatus": `Are you using tobacco?`,
  "nodesStage": `Shows if cancer has spread to nearby lymph nodes, and how many are affected:
NX : means it is not possible to assess the lymph nodes, for example, if they were previously removed.
N0 : No regional lymph node involvement.
N1: 1–3 axillary lymph nodes (in the armpit) involved or small sentinel (internal mammary nodes).
N1mi : means the cancer cells in the lymph nodes are very small (> 0.2 mm but ≤ 5 mm). These are called ‘micrometastases’.
N1a : means that there are cancer cells in 1-3 axillary lymph nodes and at least one is > 2 mm.
N1b : means there are cancer cells in the sentinel lymph nodes located behind the breastbone (the internal mammary sentinel nodes).
N1c : means there are cancer cells in 1-3 axillary lymph nodes and in the internal mammary sentinel nodes.
N2 : 4–9 axillary nodes or clinically detected internal mammary nodes without axillary involvement
N2a : means there are cancer cells in 4-9 axillary lymph nodes in the armpit, and at least one is > 2 mm.
N2b : means there are cancer cells in the internal mammary sentinel nodes, seen on a scan or felt by the doctor. There is no evidence of cancer in the axillary lymph nodes.
N3: 10+ axillary nodes, infraclavicular, or supraclavicular nodes (below the collarbone) involved; or both axillary and internal mammary nodes involved
N3a : means there are cancer cells in 10+ axillary nodes and at least one is >2 mm, or there are cancer cells in the infraclavicular, or supraclavicular nodes
N3b : means there are cancer cells in axillary lymph nodes and  internal mammary sentinel nodes.
N3c : means there are cancer cells in infraclavicular, or supraclavicular nodes (lymph nodes above the collarbone).`,
  "origin": `Specify whether your mutation is:
Somatic: acquired mutation, present only in tumor cells
Germline: inherited mutation, present in all cells of the body.`,
  "outcome": `Complete Response (CR)
No detectable M-protein in blood and urine, <5% clonal plasma cells in bone marrow, disappearance of any soft tissue plasmacytomas.
Stringent Complete Response (sCR)
Meets CR criteria plus normal free light chain ratio and no clonal plasma cells in the bone marrow by sensitive testing like flow cytometry.
Very Good Partial Response (VGPR)
≥90% reduction in serum M-protein and urine M-protein <100 mg/24h.
Partial Response (PR)
≥50% reduction in serum M-protein, ≥90% reduction in urine M-protein or <200 mg/24h, and ≥50% decrease in size of soft tissue plasmacytomas.
Minimal Residual Disease (MRD) Negativity
No detectable myeloma cells in the bone marrow by next-generation sequencing (NGS) or flow cytometry. Best predictor of long-term remission.
Stable Disease (SD)
Does not meet PR criteria but no disease progression either.
Progressive Disease (PD)
≥25% increase in serum/urine M-protein, new bone lesions or plasmacytomas, worsening organ function due to multiple myeloma.`,
  "p53Ihc": `p53 protein expression by immunohistochemistry (IHC):

Expressed as a percentage of tumor cells showing p53 protein overexpression.
High p53 IHC staining (>50%) often correlates with TP53 mutation.
TP53-mutated MCL is associated with aggressive disease and poor prognosis.
Commonly assessed at diagnosis and at relapse.`,
  "patientAge": `Enter your age in years`,
  "pdL1Assay": `Select the PD-L1 assay (clone/version) test used to measure your PD-L1 status from the list: VENTANA SP142, Dako 22C3 pharmDx, SP263, 28-8, or Other.
(These are different lab tests (antibody “clones”) used to detect PD-L1 protein. Each has its own scoring method and sensitivity).`,
  "pdL1CombinedPositiveScore": `(CPS) is a numerical score that quantifies and combines PD-L1 positive tumor (cancer) cells and PD-L1 positive immune cells (lymphocytes, macrophages) within a tumor biopsy.
Calculated using the formula: (PD-L1-positive tumor cells + PD-L1-positive immune cells) ÷ total viable tumor cells × 100
Example: if 40 tumor cells + 20 immune cells are positive, and there are 200 viable tumor cells overall → CPS = (40 + 20) ÷ 200 × 100 = 30.`,
  "pdL1IcPercentage": `% of immune cells ( lymphocytes and macrophages ) staining positive for PD-L1 in the tumor biopsy.
Example: if among immune cells in the sample, 30 of 100 stain positive, then PD-L1 IC % = 30%.`,
  "pdL1TumorCels": `% of cancer cells staining positive for PD-L1 in the tumor biopsy.
(PD-L1 is a protein found in some normal cells, and in higher amounts in certain cancer cells. It acts like a “brake” on the immune system. When PD-L1 binds to PD-1 on T cells (immune cells), it prevents those T cells from killing the PD-L1-expressing cells, including cancer cells. Drugs called immune checkpoint inhibitors block this interaction, releasing the “brake” so that T cells can attack and kill cancer cells)
Example: if 50 of 200 viable tumor cells show PD-L1, then PD-L1 tumor cells = 25%.`,
  "peripheralNeuropathyGrade": `Grade 1:
Mild symptoms such as tingling, numbness, or 'pins and needles' sensation. No interference with daily activities.
Grade 2:
Moderate symptoms including persistent numbness, pain, or burning sensations. Some interference with daily activities but not disabling.
Grade 3:
Severe symptoms such as significant pain, numbness, or loss of sensation. Major interference with daily activities and may require assistive devices for mobility.
Grade 4:
Life-threatening or disabling symptoms. Complete loss of sensation or severe pain that prevents independent functioning and requires intensive care.
Grade 5:
Death attributed to complications of peripheral neuropathy, though this is exceedingly rare.`,
  "phase": `I finds all trials in Phase I, II, III, IV. II finds phase II, III and IV. III finds III and IV`,
  "plannedTherapies": `What therapies are planned for your next treatment line or for your future care?.`,
  "plasmaCellLeukemia": `Do you have Plasma Cell Leukemia ?`,
  "plateletCount": `What is the platelet cell count?`,
  "preExistingConditionCategories": `Do you have any other preexisting conditions?`,
  "pregnancyTestResult": `Do you have a formal pregnancy test result?`,
  "priorTherapy": `Have you had prior therapy for this disease?`,
  "progesteroneReceptorStatus": `Select your Progesterone receptor (PR) status, which indicates whether breast cancer cells have receptors for progesterone:
PR–: Tumor cells do not express progesterone receptors. (<1 % of cells express PR)
PR+: Tumor cells express progesterone receptors. (10% or more).
PR+ with low expression: Small proportion of tumor cells express PR (usually 1–9 %).
PR+ with high expression: Large proportion of tumor cells express PR.`,
  "progression": `What is your cancer progression status? Active or Smoldering?`,
  "proteinExpressions": `Protein expression markers commonly assessed in CLL:

CD5+: Typically positive in CLL (T-cell marker expressed on B-cells).
CD23+: Usually positive in CLL, helps distinguish from mantle cell lymphoma.
CD38+: When positive (≥30%), associated with more aggressive disease and shorter time to treatment.
ZAP-70+: Positive expression (≥20%) indicates unmutated IGHV status and poorer prognosis.
CD19+, CD20+: B-cell markers, typically positive in CLL.
CD200+: Highly expressed in CLL, helps distinguish from other B-cell malignancies.`,
  "pulmonaryFunctionTestResult": `Do you have a pulmonary function test result?`,
  "qtcfValue": `QTcF (QT interval corrected for heart rate using Fridericia's formula):

Measures cardiac electrical activity on ECG.
Prolonged QTcF (>450 ms in males, >470 ms in females) increases risk of dangerous arrhythmias.
Some CLL therapies (especially certain kinase inhibitors) can prolong QTc.
QTcF >480-500 ms is often an exclusion criterion for clinical trials.`,
  "receptorStatusBasedType": `Receptor Status-based type`,
  "recruitmentStatus": `Is the trial recruiting patients?`,
  "refractoryStatus": `Is your disease considered refractory? If yes, which category best describes it?
-Primary Refractory: did not respond to initial therapy (or relapsed very soon after, e.g., within ~60 days).
-Secondary Refractory: responded at first, but later became resistant or relapsed after a remission period.
-Multi-Refractory: became resistant to multiple classes of drugs over time.`,
  "register": `Site where trial is posted`,
  "relapseCount": `How many relapses have you had?`,
  "renalAdequacyStatus": `Do you have Renal Adequacy?`,
  "richterTransformation": `Richter transformation refers to the transformation of chronic lymphocytic leukemia (CLL) into an aggressive lymphoma:

Most commonly transforms to Diffuse Large B-Cell Lymphoma (DLBCL) - occurs in 2-10% of CLL patients.
Less commonly transforms to Hodgkin Lymphoma - occurs in <1% of CLL patients.
Symptoms may include rapid lymph node enlargement, fever, weight loss, and worsening lab values.
Richter transformation is associated with poor prognosis and requires more aggressive treatment.`,
  "searchTreatment": `Keywords to search for in intervention/treatment. You can use boolean operators`,
  "secondLineDate": `When was the second therapy you had for your disease?`,
  "secondLineTherapy": `What was the second therapy you had for your disease?`,
  "serumBeta2MicroglobulinLevel": `Serum β2-microglobulin is a prognostic marker in CLL:

Normal range is typically <2.5 mg/L.
Elevated levels (≥4 mg/L) are associated with more advanced disease and poorer prognosis.
Part of the CLL International Prognostic Index (CLL-IPI).
Reflects tumor burden and is a marker of disease activity.`,
  "serumBilirubinLevelDirect": `What is the direct serum bilirubin level?`,
  "serumBilirubinLevelTotal": `What is the total serum bilirubin level?`,
  "serumCalciumLevel": `What is the serum calcium level?`,
  "serumCreatinineLevel": `What is the blood serum creatinine level?`,
  "spleenSize": `Size of the spleen:

Measured in centimeters below the costal margin by physical exam, or in total length by CT scan.
Normal spleen is not palpable below costal margin and measures <13 cm by CT.
Used to assess tumor burden and monitor disease progression.
Spleen enlargement is considered in Binet staging.`,
  "splenomegaly": `Splenomegaly means enlarged spleen:

In CLL, the spleen may be infiltrated with leukemia cells.
Clinically defined as spleen palpable >2 cm below the left costal margin.
By imaging (CT), normal spleen is <13 cm in length.
Massive splenomegaly (>20 cm) can cause symptoms like abdominal discomfort and early satiety.`,
  "sponsor": `Organization organizing the trial`,
  "stage.breast.cancer": `Stages of Breast Cancer (BC):
Stage 0:
Definition: Noninvasive cancer, also known as carcinoma in situ (e.g. ductal carcinoma in situ, DCIS). Abnormal (pre-cancerous or non-invasive) cells are confined within ducts or lobules and have not invaded surrounding breast tissue.
Example: DCIS; Paget’s disease of the nipple (without invasive cancer).
Prognosis: Very favorable. Most cases are curable when treated.
Stage I:
Definition: Early stage (localized, small tumor, no or minimal lymph node involvement. Invasive cancer (i.e. the cancer cells have begun to invade normal breast tissue) but small in size.
Usually no or minimal lymph node involvement.
Example: Tumor ≤ 2 cm diameter and no lymph node spread (or only very tiny, ‘micro-metastases’ in nodes)
Prognosis: Excellent. survival rates are very high, often around 99% or close when detected and treated early.
Stage II:
Definition: Early but larger, and/or limited regional spread (max 3 regional lymph nodes)
Examples:
Tumor ≤ 1 cm diameter and at least 1 to 3 lymph nodes spread. Or
Tumor 2 to 5 cm and maximum 3 lymph nodes spread. Or
Tumor >= 5 cm and NO lymph nodes spread.
Prognosis: Very good; slightly lower than Stage I but still favorable, depending on exact biology, treatment
Stage III:
Definition: Locally advanced disease (Larger tumor, extensive lymph node involvement or spread to nearby tissues, but not distant sites metastasis)
Example:
Tumor of any size has spread to more than 4 lymph nodes. Or
Tumor > 5cm and only has spread to 1-3 lymph nodes. Or
Tumor of any size and the disease have spread to the chest wall.
Inflammatory breast cancer with no distant metastasis.
Prognosis: More challenging than stage I or II. Requires more aggressive treatment (surgery, radiation, chemotherapy, etc.).
Stage IV:
Definition: Metastatic breast cancer: cancer has spread beyond the breast and nearby lymph nodes to distant organs (such as bones, liver, lung, brain).
Example: Breast cancer spreading to bone, or to lungs, or to liver, etc.
Prognosis: More severe. Outcome depends a lot on where metastases are, patient health, cancer subtype, available treatments. Considered incurable in many cases, though treatment may prolong life, improve quality of life, and sometimes achieve remission.`,
  "stage.follicular.lymphoma": `Stages of Follicular Lymphoma (FL)
Stage I:
Definition: Involvement of a single lymph node region or a single extralymphatic organ or site (referred to as "extranodal").
Example: Lymph nodes in one area of the neck are affected.
Prognosis: Typically excellent when treated, with long periods of remission.
Stage II:
Definition: Involvement of two or more lymph node regions on the same side of the diaphragm, or local involvement of a single extralymphatic organ and nearby lymph nodes.
Example: Affected lymph nodes in the neck and chest, but still on the same side of the diaphragm.
Prognosis: Good, particularly with localized treatment like radiation or immunotherapy.
Stage III:
Definition: Involvement of lymph node regions on both sides of the diaphragm, possibly including the spleen or localized involvement of an extralymphatic organ.
Example: Affected lymph nodes in the chest and abdomen.
Prognosis: More widespread, but still manageable with systemic treatments (e.g., chemotherapy, targeted therapy).
Stage IV:
Definition: Disseminated involvement of one or more extralymphatic organs (e.g., bone marrow, liver) with or without lymph node involvement.
Example: Disease found in the bone marrow and liver.
Prognosis: Requires comprehensive systemic treatment but is still considered treatable due to the indolent nature of FL.`,
  "stage.mantle.cell.lymphoma": `Stages of Mantle Cell Lymphoma (MCL) — Ann Arbor staging system:

Stage I:
Involvement of a single lymph node region or a single extralymphatic organ or site.
Prognosis: Uncommon at presentation; localized disease with relatively favorable outcomes when treated.

Stage II:
Involvement of two or more lymph node regions on the same side of the diaphragm.
Prognosis: Intermediate; systemic therapy typically required.

Stage III:
Involvement of lymph node regions on both sides of the diaphragm.
Prognosis: Advanced disease; requires systemic chemoimmunotherapy.

Stage IV:
Disseminated involvement of extralymphatic organs (e.g., bone marrow, liver, spleen) with or without lymph node involvement.
Prognosis: Most common presentation (~70% of MCL cases); requires aggressive systemic therapy.

Note: The blastoid and pleomorphic variants are associated with more aggressive clinical course regardless of stage.`,
  "stage.multiple.myeloma": `Enter the stage of your disease by selecting from list of I to III:
Stage I:
Beta-2 Microglobulin: ≤3.5 mg/L.
Albumin: ≥3.5 g/dL.
No high-risk cytogenetic abnormalities (e.g., del(17p), t(4;14), t(14;16)).
Normal LDH levels.
Prognosis: Best among the stages, with a median survival of over 8 years in some cases.
Stage II:
Intermediate levels of beta-2 microglobulin, albumin, or LDH.
Prognosis: Median survival of approximately 5–6 years.
Stage III:
Beta-2 Microglobulin: >5.5 mg/L.
High-risk cytogenetic abnormalities or elevated LDH levels.
Prognosis: Poorer, with a median survival of 2–3 years without effective treatment.`,
  "stagingModalities": `Refers to the tests or methods used to determine stage:
c --> Clinical.
p --> Pathological.
yp --> Pathological after Neoadjuvant therapy.`,
  "stemCellTransplantHistory": `Have you ever had, or are you currently planned for, a specific type of stem cell transplant?
Choose the option that best applies to you from the list:

• None
• Completed ASCT
• Eligible for ASCT
• Ineligible for ASCT
• Completed Allogeneic SCT
• Pre-ASCT
• Never Received SCT
• SCT-Ineligible
• Relapsed Post-ASCT
• Relapsed Post-Allogeneic SCT
• Completed Tandem SCT`,
  "studyType": `Filter by study type (Interventional, Observational)`,
  "supportiveTherapies.date": `When did you receive this supportive therapy?.`,
  "supportiveTherapies.therapy": `What supportive, maintenance, or adjuvant treatments have you received across all your therapy lines?.`,
  "tnbcStatus": `‘Yes’ only if the tumor is ER-negative, PR-negative, and HER2-negative (all three negative); otherwise, ‘No’.`,
  "toxicityGrade": `Grade 0 (None): No symptoms or adverse effects.
Grade 1 (Mild): Mild symptoms; intervention not usually required.
Grade 2 (Moderate): Moderate symptoms; minimal, local, or noninvasive intervention indicated.
Grade 3 (Severe): Severe or medically significant symptoms; hospitalization or invasive intervention may be required.
Grade 4 (Life-threatening): Life-threatening consequences; urgent intervention required.`,
  "tp53Disruption": `TP53 aberration in CLL includes TP53 mutation, deletion of 17p (del17p), or p53 protein overexpression (IHC ≥50%):

Present in 5-10% of treatment-naïve CLL and up to 40% of relapsed/refractory cases.
Associated with very poor prognosis with chemoimmunotherapy (median survival <3 years).
Indicates resistance to chemotherapy and some targeted therapies.
TP53-aberrant CLL requires novel agents like BTK inhibitors, BCL-2 inhibitors, or CAR-T therapy.
Considered highest-risk disease feature.`,
  "treatmentRefractoryStatus": `Is your disease considered refractory?`,
  "trialType": `Search by Trial Type`,
  "tumorBurden": `Overall tumor burden in CLL refers to the total amount of cancer in the body:

Low: Minimal lymphadenopathy, normal or slightly enlarged spleen, low lymphocyte count.
Intermediate: Moderate lymphadenopathy, moderate splenomegaly, intermediate lymphocyte count.
High: Extensive lymphadenopathy, massive splenomegaly and/or hepatomegaly, high lymphocyte count, possible bone marrow involvement.`,
  "tumorGrade": `Grade 1 (Low-Grade):
Cells look similar to normal cells.
Tumor grows slowly and is less likely to spread.
Often considered the least aggressive.
Grade 2 (Intermediate-Grade):
Cells are moderately different from normal cells.
Tumor grows at a moderate rate and has some potential to spread.
Grade 3 (High-Grade):
Cells look very different from normal cells.
Tumor grows quickly and is more likely to spread, indicating a more aggressive cancer.
Grade 4 (Undifferentiated or Anaplastic):
Cells look very abnormal and lack normal structure.
Tumor grows and spreads rapidly, often considered the most aggressive.`,
  "tumorStage": `Describes the size of the main tumor and whether it has grown into nearby tissue.
Tx : Primary Tumor cannot be assessed
T0 : No evidence of primary tumor
Tis : Carcinoma in situ (non-invasive) - includes DCIS (ductal carcinoma in situ), LCIS (lobular carcinoma in situ), or 
Paget's disease without a tumor.
T1 : (Invasive) Tumor ≤ 2 cm
T1mi : Tumor ≤ 0.1 cm across
T1a : 0.1 cm > Tumor ≤ 0.5 cm
T1b : 0.5 cm > Tumor ≤ 1 cm
T1c  : 1 cm > Tumor ≤ 2 cm
T2 : (Invasive) Tumor > 2 cm but ≤ 5 cm
T3 : (Invasive) Tumor > 5 cm
T4 : (Invasive) Tumor of any size that invades chest wall or skin, or presents as inflammatory breast cancer
T4a : means the cancer has spread into the chest wall
T4b : means the cancer has spread into the skin and the breast might be swollen
T4c : means the cancer has spread to both the skin and the chest wall
T4d : means inflammatory carcinoma.`,
  "validatedOnly": `Select yes to show Validated Trials Only`,
  "variant": `Choose the specific genetic change (Mutation Variant) in the selected gene, written in standard DNA or protein notation.`,
  "weight": `Enter your weight`,
  "whiteBloodCellCount": `What is the total white blood cell count?`,
};
