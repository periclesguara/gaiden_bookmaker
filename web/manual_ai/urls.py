from django.urls import path

from . import views

app_name = "manual_ai"
urlpatterns = [
    path("", views.home, name="home"),
    path("editions/<int:edition_id>/", views.edition, name="edition"),
    path("translate/", views.stage, {"stage": "translate"}, name="translate"),
    path("refine/", views.stage, {"stage": "refine"}, name="refine"),
    path("polish/", views.stage, {"stage": "polish"}, name="polish"),
]
