from exact_matching.patient_info.convertors.serum_creatinine_convertor import SerumCreatinineConvertor


class EgfrCalculator:
    @staticmethod
    def call(patient_info):
        scr = patient_info.serum_creatinine_level
        age = patient_info.patient_age
        gender = patient_info.gender
        if not scr or not age or not gender:
            return None

        if gender.lower() == 'f':
            kappa = 0.7
            alpha = -0.241
            gender_factor = 1.012
        elif gender.lower() == 'm':
            kappa = 0.9
            alpha = -0.302
            gender_factor = 1.000
        else:
            return None

        scr_mg_dl = SerumCreatinineConvertor.call(scr, patient_info.serum_creatinine_level_units.lower(), 'mg/dl')
        if not scr_mg_dl:
            return None

        scr_kappa_ratio = float(scr_mg_dl) / float(kappa)
        min_ratio = min(scr_kappa_ratio, 1)
        max_ratio = max(scr_kappa_ratio, 1)

        egfr = 142 * (min_ratio ** alpha) * (max_ratio ** -1.200) * (0.9938 ** age) * gender_factor
        return round(egfr, 2)
