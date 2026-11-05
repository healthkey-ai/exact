import inflection
import re

ATTRIBUTE_NAME_MAPPING = {
    'age_low_limit': 'ageMin',
    'age_high_limit': 'ageMax',
    'kappa_flc': 'kappaFLC',
    'lambda_flc': 'lambdaFLC',
    'meets_crab': 'meetsCRAB',
    'meets_slim': 'meetsSLIM',
    'meets_gelf': 'meetsGELF',
}


class AttributeNames:
    @staticmethod
    def get_by_camel_case(attr_name: str) -> str:
        def key_by_value(name):
            try:
                return list(ATTRIBUTE_NAME_MAPPING.keys())[list(ATTRIBUTE_NAME_MAPPING.values()).index(name)]
            except ValueError:
                return None

        return key_by_value(attr_name) or inflection.underscore(attr_name)

    @staticmethod
    def get_by_snake_case(attr_name: str) -> str:
        return ATTRIBUTE_NAME_MAPPING.get(
            attr_name,
            inflection.camelize(attr_name, uppercase_first_letter=False)
        )
        
    @staticmethod
    def humanize(attr_name: str, title: bool = True) -> str:
        """
        Convert camelCase, PascalCase, or snake_case into readable text,
        preserving existing uppercase acronyms.

        Examples:
            contraceptiveUse -> Contraceptive Use
            age_minimum -> Age Minimum
            kappaFLC -> Kappa FLC
            Lambda FLC -> Lambda FLC
        """
        # Insert underscores only on lowercase→uppercase boundaries
        word = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', attr_name)
        word = re.sub(r'_id$', '', word)

        # Replace underscores with spaces
        result = word.replace('_', ' ')

        # Lowercase words unless they are all-caps (acronyms)
        words = []
        for w in result.split():
            if w.isupper():  # keep acronyms like FLC, ECOG
                words.append(w)
            elif title:
                words.append(w.capitalize())
            else:
                words.append(w)
        return ' '.join(words)
