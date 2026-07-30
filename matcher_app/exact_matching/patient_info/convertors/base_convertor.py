from quantityfield.units import ureg

if 'cell' not in ureg:
    ureg.define('@alias gram = cell')  # hack the pint lib, let pint think cell is gram

class BaseConvertor:
    @staticmethod
    def call(value, from_unit, to_unit):
        if from_unit is None or from_unit == '' or to_unit is None or to_unit == '':
            return value

        from_unit = from_unit.lower()
        to_unit = to_unit.lower()
        if from_unit == to_unit:
            return value

        try:
            return ureg.Quantity(float(value), from_unit).to(to_unit).magnitude
        except Exception as e:
            return value
