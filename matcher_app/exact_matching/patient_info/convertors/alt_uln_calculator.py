# ALT (U/L)
#
# Ethnicity	Men	Women
# White	    ~45	~34
# Black	    ~48	~37
# Asian	    ~40	~32
# Hispanic	~50	~38

ALT_ULN_MAPPING = {
    'caucasian_or_european': {
        'M': 45,
        'F': 34
    },
    'Caucasian/European': {
        'M': 45,
        'F': 34
    },

    'african_or_black': {
        'M': 48,
        'F': 37
    },
    'African/Black': {
        'M': 48,
        'F': 37
    },

    'asian': {
        'M': 40,
        'F': 32
    },
    'Asian': {
        'M': 40,
        'F': 32
    },

    'native_american': {
        'M': 45,
        'F': 34
    },
    'Native American': {
        'M': 45,
        'F': 34
    }
}


class AltUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity
        gender = patient_info.gender

        if ethnicity in ALT_ULN_MAPPING and gender in ALT_ULN_MAPPING[ethnicity]:
            return float(value) / ALT_ULN_MAPPING[ethnicity][gender]
        return None
