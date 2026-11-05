# AST (U/L)
#
# Ethnicity	Men	Women
# White 	~40	~35
# Black	    ~42	~36
# Asian	    ~38	~33
# Hispanic	~41	~35

AST_ULN_MAPPING = {
    'caucasian_or_european': {
        'M': 40,
        'F': 35
    },
    'Caucasian/European': {
        'M': 40,
        'F': 35
    },

    'african_or_black': {
        'M': 42,
        'F': 36
    },
    'African/Black': {
        'M': 42,
        'F': 36
    },

    'asian': {
        'M': 38,
        'F': 33
    },
    'Asian': {
        'M': 38,
        'F': 33
    },

    'native_american': {
        'M': 40,
        'F': 35
    },
    'Native American': {
        'M': 40,
        'F': 35
    }
}


class AstUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity
        gender = patient_info.gender

        if ethnicity in AST_ULN_MAPPING and gender in AST_ULN_MAPPING[ethnicity]:
            return float(value) / AST_ULN_MAPPING[ethnicity][gender]
        return None
