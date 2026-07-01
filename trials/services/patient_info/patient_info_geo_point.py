import django_countries
import pgeocode
from geopy.geocoders import Nominatim

from django.contrib.gis.geos import Point

COUNTRIES_LIST = django_countries.countries.countries
COUNTRIES_MAPPING = {
    'United States': 'US',
    'Russian Federation': 'RU'
}


class PatientInfoGeoPoint:
    @staticmethod
    def country_code_by_country_code_or_name(country_code_by_country_code_or_name):
        if not country_code_by_country_code_or_name:
            return

        if country_code_by_country_code_or_name.upper() in COUNTRIES_LIST.keys():
            return country_code_by_country_code_or_name.upper()

        if country_code_by_country_code_or_name in COUNTRIES_MAPPING:
            return COUNTRIES_MAPPING[country_code_by_country_code_or_name]

        try:
            idx = list(COUNTRIES_LIST.values()).index(country_code_by_country_code_or_name)
            if idx:
                return list(COUNTRIES_LIST.keys())[idx]
        except ValueError:
            return None

    @staticmethod
    def point_by_country_and_postal_code(country, postal_code):
        if len(str(country)) == 0 or len(str(postal_code)) == 0:
            return None

        country_code = PatientInfoGeoPoint.country_code_by_country_code_or_name(country)
        if not country_code:
            return None

        if country_code == 'US':
            postal_code = str(postal_code)[0:5]

        nomi = pgeocode.Nominatim(country_code)
        geo_name_record = nomi.query_postal_code(postal_code)

        # seems it's a numpy or pandas format
        latitude = geo_name_record['latitude']
        longitude = geo_name_record['longitude']

        if str(latitude) == 'nan' or str(longitude) == 'nan':
            return None

        try:
            return Point(longitude, latitude, srid=4326)
        except TypeError:
            return None

    @staticmethod
    def country_and_postal_code_by_geolocation(longitude, latitude):
        geolocator = Nominatim(user_agent="exact.app")
        location = geolocator.reverse(f"{latitude}, {longitude}")
        address = location.raw.get('address', {})
        country_code = address.get('country_code')
        country_name = None
        if country_code:
            country_name = COUNTRIES_LIST.get(str(country_code).upper())

        return {
            'country': country_name or address.get('country'),
            'country_code': address.get('country_code'),
            'postal_code': address.get('postcode')
        }

    @staticmethod
    def update_country_and_postal_code_by_geolocation(longitude, latitude, pi_id):
        from trials.services.patient_info.patient_info import PatientInfo
        country_and_postal_code = PatientInfoGeoPoint.country_and_postal_code_by_geolocation(longitude, latitude)
        # force skip signals
        PatientInfo.objects.filter(id=pi_id).update(country=country_and_postal_code['country'],
                                                    postal_code=country_and_postal_code['postal_code'])

