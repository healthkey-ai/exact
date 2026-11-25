# Serum Direct Bilirubin (mg/dL)
#
# Ethnicity	Men/Women
# White	    ~0.3
# Black	    ~0.25
# Asian	    ~0.35
# Hispanic	~0.3


BILI_D_ULN_MAPPING = {
    'caucasian_or_european': 0.3,
    'Caucasian/European': 0.3,

    'african_or_black': 0.25,
    'African/Black': 0.25,

    'asian': 0.35,
    'Asian': 0.35,

    'native_american': 0.3,
    'Native American': 0.3
}


class BiliDUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity

        if ethnicity in BILI_D_ULN_MAPPING:
            return float(value) / BILI_D_ULN_MAPPING[ethnicity]
        return None
