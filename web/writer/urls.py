from django.urls import path

from . import views

app_name = "writer"
urlpatterns = [
    path("", views.home, name="home"),
    path("works/", views.works, name="works"),
    path("works/<int:work_id>/", views.work_detail, name="work_detail"),
    path("manuscripts/<int:manuscript_id>/", views.manuscript_detail, name="manuscript"),
    path("manuscripts/<int:manuscript_id>/versions/<int:version_id>/", views.version_preview, name="version_preview"),
    path("manuscripts/<int:manuscript_id>/versions/<int:version_id>/export/", views.export_version, name="export"),
    path("manuscripts/<int:manuscript_id>/versions/<int:version_id>/promote/", views.promote, name="promote"),
]
