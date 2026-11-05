from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from trials.models import CytogenicMarker, MolecularMarker
from trials.services.markers_mapper import MarkersMapper


class LoadMarkers:
    def load_all(self):
        # This is a standalone/test fixture, not a catalog repair command.
        if 'trials' in settings.DATABASES:
            raise ImproperlyConfigured('Marker seeding is standalone-only; CB owns the trials catalog.')
        data = MarkersMapper().data()
        for model, category in (
            (CytogenicMarker, 'Cytogenic Markers'),
            (MolecularMarker, 'Molecular Markers'),
        ):
            for code, obj in data.items():
                if category in obj['categories']:
                    model.objects.update_or_create(code=code, defaults={
                        'title': obj['name'], 'description': obj['description'],
                    })
