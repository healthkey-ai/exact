# SCr (mg/dL)
#
# Ethnicity	Men 	Women
# White	    ~1.3	~1.1
# Black	    ~1.4	~1.2
# Asian	    ~1.2	~1.0
# Hispanic	~1.3	~1.1

SCR_ULN_MAPPING = {
    'caucasian_or_european': {
        'M': 1.3,
        'F': 1.1
    },
    'Caucasian/European': {
        'M': 1.3,
        'F': 1.1
    },

    'african_or_black': {
        'M': 1.4,
        'F': 1.2
    },
    'African/Black': {
        'M': 1.4,
        'F': 1.2
    },

    'asian': {
        'M': 1.2,
        'F': 1.0
    },
    'Asian': {
        'M': 1.2,
        'F': 1.0
    },

    'native_american': {
        'M': 1.3,
        'F': 1.1
    },
    'Native American': {
        'M': 1.3,
        'F': 1.1
    }
}


class ScrUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity
        gender = patient_info.gender

        if ethnicity in SCR_ULN_MAPPING and gender in SCR_ULN_MAPPING[ethnicity]:
            return float(value) / SCR_ULN_MAPPING[ethnicity][gender]
        return None
