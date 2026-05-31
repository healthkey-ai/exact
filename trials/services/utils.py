from glom import glom


def disease_attr_applies(attr_disease, patient_disease) -> bool:
    """True if a mapping entry's `disease` restriction includes the patient.

    `attr_disease` is the `disease` value from a `USER_TO_TRIAL_ATTRS_MAPPING`
    entry — a single disease code (str) or a collection of codes. Replaces
    the inline `disease_code in trial_attr_meta["disease"]` checks at the
    matcher, queryset, mapper, trial-detail, and explainer call sites (#43),
    which silently substring-match when `attr_disease` is a string
    (`'M' in 'MM'` is True), so adding a new disease code that's a
    substring of an existing one would have caused subtle cross-disease
    pollution.
    """
    if isinstance(attr_disease, (list, tuple, set)):
        return patient_disease in attr_disease
    return attr_disease == patient_disease


def glom_or_none(data, key):
    try:
        return glom(data, key)
    except KeyError:
        return None


def to_date(value):
    if not value:
        return None
    parts = value.split('-')
    if len(parts) == 2:
        parts.append('01')
        return '-'.join(parts)
    else:
        return value


def get_overlap(a, b):
    def list_of_str(values):
        return [str(x) for x in values]

    return list(set(list_of_str(a)) & set(list_of_str(b)))
