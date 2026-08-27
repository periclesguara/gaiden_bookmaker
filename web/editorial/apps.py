from django.apps import AppConfig


class EditorialConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "editorial"

    def ready(self):
        from editorial.storefront_availability import install_sales_channels_field

        install_sales_channels_field()
