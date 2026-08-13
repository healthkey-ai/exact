from trials.models import CytogenicMarker, MolecularMarker
from trials.services.markers_mapper import MarkersMapper


class LoadMarkers:
    def load_all(self):
        mapper = MarkersMapper()
        self._load(CytogenicMarker, mapper.cytogenic())
        self._load(MolecularMarker, mapper.molecular())

    def _load(self, model, data):
        for code, obj in data.items():
            model.objects.update_or_create(
                code=code,
                defaults={'title': obj['name'], 'description': obj['description']},
            )
