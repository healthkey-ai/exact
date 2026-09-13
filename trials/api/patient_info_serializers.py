import functools

from django.utils.functional import cached_property as django_cached_property


def _cached_property_names(cls) -> set:
    """Every `cached_property` on the class and its bases.

    A `cached_property` stores its result in the INSTANCE dict under its own
    name on first access, so anything that reads one leaves a live service
    object sitting in `vars(instance)` beside the patient's data. This
    serializer copies that dict wholesale, so the difference between a
    response that renders and a 500 was whether anything had touched
    `attributes_service` yet — and `geolocation` returns `self`, which would
    have been worse. Derived from the class rather than a hardcoded list, so a
    cached property added later is excluded by existing.
    """
    # Both flavours: `PatientInfo` uses `functools.cached_property` and other
    # code in this tree uses Django's. They cache identically and neither
    # belongs in a response.
    kinds = (functools.cached_property, django_cached_property)
    names = set()
    for klass in cls.__mro__:
        for name, value in vars(klass).items():
            if isinstance(value, kinds):
                names.add(name)
    return names


class PatientInfoSerializer:
    """
    Serializes an in-memory PatientInfo instance for API responses.

    PatientInfo is a plain Python class (not a Django model); this serializer
    is used purely for output — reading from the in-memory object built by
    resolve_patient_info().
    """

    def __init__(self, instance, **kwargs):
        self.instance = instance

    @property
    def data(self):
        return self.to_representation(self.instance)

    #: Derived values that are not JSON, and whose source IS. `geo_point` is a
    #: GEOS `Point` that `normalize_patient_info` builds from `latitude` and
    #: `longitude` — or from `country` + `postal_code` — and both of those stay
    #: on the instance, so dropping it costs a client nothing and leaving it in
    #: costs them the whole response: `JSONRenderer` raises on it, which is a
    #: 500 for every patient who has a location. Location is a first-class
    #: filter here, so that is most of them.
    DERIVED_NOT_JSON = ('geo_point',)

    def to_representation(self, instance):
        skip = set(_cached_property_names(type(instance))) | set(self.DERIVED_NOT_JSON)
        d = {
            k: v for k, v in vars(instance).items()
            if not k.startswith('_') and k not in skip
        }
        if getattr(instance, 'no_pre_existing_conditions', False):
            d['preExistingConditionCategories'] = ['none']
        else:
            cats = getattr(instance, '_pre_existing_condition_categories', [])
            d['preExistingConditionCategories'] = [c.code for c in cats]
        return d


class GraphPatientInfoSerializer(PatientInfoSerializer):
    """Strip the heavy fields from PatientInfoSerializer for the graph endpoint."""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop('details', None)
        return data
