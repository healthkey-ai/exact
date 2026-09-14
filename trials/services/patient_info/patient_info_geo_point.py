import logging

import django_countries
import pgeocode
from geopy.geocoders import Nominatim

from django.contrib.gis.geos import Point

logger = logging.getLogger(__name__)

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

        # Coerced, not type-checked. `country` comes straight off the inline
        # `patient_info` payload — `_build_in_memory` coerces dates, numerics
        # and JSON fields, not this — so `{"country": 123}` reached `.upper()`
        # and raised `AttributeError` out of `normalize_patient_info`, on every
        # patient-context endpoint. Same defect class as the pgeocode one
        # below, same call chain (#391).
        #
        # An `isinstance` guard was here first and earned nothing: a mutation
        # test removing it killed no test, because `str()` alone already turns
        # every non-country into a string that matches no country and resolves
        # to None. It also has to admit `Promise` — `COUNTRIES_LIST`'s own
        # values are lazy translation proxies — so the guard was one more
        # thing to keep right for no behaviour of its own.
        country_code_by_country_code_or_name = str(country_code_by_country_code_or_name)

        if country_code_by_country_code_or_name.upper() in COUNTRIES_LIST.keys():
            return country_code_by_country_code_or_name.upper()

        if country_code_by_country_code_or_name in COUNTRIES_MAPPING:
            return COUNTRIES_MAPPING[country_code_by_country_code_or_name]

        try:
            idx = list(COUNTRIES_LIST.values()).index(country_code_by_country_code_or_name)
        except ValueError:
            return None
        # `if idx:` here, which read index 0 as "not found" — and index 0 is
        # Afghanistan. `.index()` raises when the name is absent, so reaching
        # this line already means it was found; the truthiness test could only
        # ever be wrong. An Afghan patient lost their country entirely, not
        # just their geo point: `_normalize_geo_point` clears `country` when
        # this returns None.
        return list(COUNTRIES_LIST.keys())[idx]

    @staticmethod
    def point_by_country_and_postal_code(country, postal_code):
        if len(str(country)) == 0 or len(str(postal_code)) == 0:
            return None

        country_code = PatientInfoGeoPoint.country_code_by_country_code_or_name(country)
        if not country_code:
            return None

        if country_code == 'US':
            postal_code = str(postal_code)[0:5]

        # pgeocode ships postal data for 95 of the 249 countries this service
        # offers as valid input, and raises ValueError — it does not return
        # nothing — for the other 154:
        #
        #     ValueError: country=EG is not a known country code
        #
        # Unguarded, that reached the caller. This function sits on the
        # stateless patient path (`resolve_patient_info` -> `_build_in_memory`
        # -> `normalize_patient_info` -> `_normalize_geo_point`), so any
        # request whose patient carried a country and a postal code in that
        # gap answered 500 — on `/trials/`, on the detail endpoint, on the
        # graph, on anything that resolves a patient (#391).
        #
        # ASKED, not caught. Catching `ValueError` to mean "unsupported
        # country" looks equivalent and is not: `pandas.errors.EmptyDataError`
        # and `ParserError` are both ValueError SUBCLASSES, and pgeocode reads
        # its on-disk cache with `pd.read_csv` — so a cache truncated by a
        # restart mid-download raised one of those for a country that IS
        # supported, and the handler logged "no postal data for US". False, at
        # debug level, while every US patient silently lost distance from then
        # on. Review caught it; the download takes minutes, which is a wide
        # window for that restart.
        #
        # Reading the support list up front separates the two questions, so
        # anything the call still raises is genuinely unexpected and is logged
        # as such.
        supported = getattr(pgeocode, 'COUNTRIES_VALID', None)
        if supported is not None and country_code not in supported:
            # Not an incident: a patient who lives somewhere pgeocode has no
            # data for is an ordinary patient.
            logger.debug('pgeocode has no postal data for %s', country_code)
            return None

        try:
            nomi = pgeocode.Nominatim(country_code)
            geo_name_record = nomi.query_postal_code(postal_code)
        except Exception:
            # A supported country that still failed: a corrupt cache, a failed
            # download, a pgeocode whose `COUNTRIES_VALID` this version could
            # not read. Not a reason to fail a clinical search, but somebody
            # should see it.
            logger.warning(
                'pgeocode failed for country=%s; continuing without a geo point',
                country_code,
                exc_info=True,
            )
            return None

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

