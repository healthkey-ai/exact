from exact_matching.patient_info.convertors.simple_c_to_si_convertor import SimpleCToSiConvertor


class SerumCreatinineConvertor:
    @staticmethod
    def call(value, from_unit, to_unit):
        return SimpleCToSiConvertor.call(value, from_unit, to_unit, cf=88.42)
