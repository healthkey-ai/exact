class AlpUlnCalculator:
    @staticmethod
    def call(value, patient_info):
        ethnicity = patient_info.ethnicity
        gender = patient_info.gender
        age = patient_info.patient_age

        # https://github.com/cancerbot-org/cancerbot/issues/932#issuecomment-2782302173
        def uln_val():
            if age < 10:
                return 400
            elif 10 <= age < 15:
                return 500 if gender == "M" else 400
            elif 15 <= age < 20:
                return 400 if gender == "M" else 300
            elif age >= 60:
                return 150 if gender == "M" else 130
            else:
                base_uln = 130 if gender == "M" else 105
                # Ethnicity adjustment
                if ethnicity == "African/Black":
                    return base_uln + 10
                elif ethnicity == "Asian":
                    return base_uln - 10
                else:
                    return base_uln

        if ethnicity and ethnicity and age:
            return float(value) / uln_val()
        return None
