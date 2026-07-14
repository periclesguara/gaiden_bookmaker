from django.apps import AppConfig


class AuthorStudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Existing Django apps are imported from the web/ directory as top-level
    # packages. Keep the same module identity to avoid duplicate model classes.
    name = "author_studio"
    verbose_name = "Gaiden Author Studio"
