"""A patient without a postal code must not trigger a geocoder download.

`str(None)` is the four-character string "None", so a length check on
`str(postal_code)` accepted a null value and made pgeocode download a country
extract on every request (minutes, and it fails offline). The guard must treat
a missing country or postal code as missing.
"""
from trials.services.patient_info.patient_info_geo_point import PatientInfoGeoPoint


def test_missing_postal_code_is_not_geocoded(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('pgeocode must not be constructed for a missing value')

    monkeypatch.setattr('pgeocode.Nominatim', fail)

    assert PatientInfoGeoPoint.point_by_country_and_postal_code('United States', None) is None
    assert PatientInfoGeoPoint.point_by_country_and_postal_code(None, '90210') is None
    assert PatientInfoGeoPoint.point_by_country_and_postal_code(None, None) is None
