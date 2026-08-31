# Bilirubin, (total and direct) ==>  CF: 17.1 meaning µmol/L = 17.1 * mg/dL
# Serum Creatinine ==> CF: 88.42 meaning µmol/L = 88.42 * mg/dL
# Serum Calcium Level ==> CF: 0.2495 meaning µmol/L = 0.2495 * mg/dL

class SimpleCToSiConvertor:
    @staticmethod
    def call(value, from_unit, to_unit, cf):
        if from_unit is None or from_unit == '' or to_unit is None or to_unit == '':
            return value

        from_unit = from_unit.lower()
        to_unit = to_unit.lower()
        if from_unit == to_unit:
            return value

        try:
            if from_unit == "mg/dl" and to_unit == "micromoles/l":
                return float(value) * cf
            else:
                return float(value) / cf
        except Exception:
            return value
