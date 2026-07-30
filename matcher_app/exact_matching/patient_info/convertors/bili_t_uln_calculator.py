# Serum Total Bilirubin (mg/dL)
#
# Ethnicity	Men/Women
# White	    ~1.2
# Black	    ~1.0
# Asian	    ~1.3
# Hispanic	~1.2


BILI_T_ULN_MAPPING = {
    'caucasian_or_european': 1.2,
    'Caucasian/European': 1.2,

    'african_or_black': 1.0,
    'African/Black': 1.0,

    'asian': 1.3,
    'Asian': 1.3,

    'native_american': 1.2,
    'Native American': 1.2
}


class BiliTUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity

        if ethnicity in BILI_T_ULN_MAPPING:
            return float(value) / BILI_T_ULN_MAPPING[ethnicity]
        return None
