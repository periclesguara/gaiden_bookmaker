from django.apps import AppConfig


class EditorialConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'editorial'

    def ready(self) -> None:
        import editorial.signals  # noqa: F401
