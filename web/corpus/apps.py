from django.apps import AppConfig, apps
from django.db.models.signals import post_save


def initialize_backend(sender, instance, created, **kwargs):
    if created:
        from .models import ProjectCorpus
        ProjectCorpus.objects.create(project=instance, backend="CANONICAL_CORPUS")


class CorpusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "corpus"

    def ready(self):
        post_save.connect(initialize_backend, sender=apps.get_model("writer", "StoryProject"),
                          dispatch_uid="corpus.new_project_backend")
