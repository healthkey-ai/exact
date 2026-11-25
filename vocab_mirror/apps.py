from django.apps import AppConfig


class VocabMirrorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'vocab_mirror'
    verbose_name = 'OMOP vocabulary mirror'
