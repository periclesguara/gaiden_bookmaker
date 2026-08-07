from django.urls import path

from . import legacy_views, views

app_name = "writer"

urlpatterns = [
    path("", views.home, name="home"),
    path("sources/", views.sources, name="sources"),
    path("sources/scan/", views.scan_sources, name="scan_sources"),
    path("sources/normalize/", views.normalize_sources, name="normalize_sources"),
    path("projects/new/", views.project_edit, name="project_new"),
    path("projects/<int:project_id>/", views.project_detail, name="project_detail"),
    path("projects/<int:project_id>/edit/", views.project_edit, name="project_edit"),
    path("projects/<int:project_id>/sources/", views.project_sources, name="project_sources"),
    path("projects/<int:project_id>/vectorize/", views.vectorize, name="vectorize"),
    path("chapters/<int:chapter_id>/", views.chapter_detail, name="chapter_detail"),
    path("chapters/<int:chapter_id>/edit/", views.chapter_edit, name="chapter_edit"),
    path("chapters/<int:chapter_id>/generate/", views.generate, name="generate"),
    path("chapters/<int:chapter_id>/finalize/", views.finalize, name="finalize"),
    path("works/", legacy_views.works, name="works"),
    path("works/<int:work_id>/", legacy_views.work_detail, name="work_detail"),
    path("manuscripts/<int:manuscript_id>/", legacy_views.manuscript_detail, name="manuscript"),
    path(
        "manuscripts/<int:manuscript_id>/versions/<int:version_id>/",
        legacy_views.version_preview,
        name="version_preview",
    ),
    path(
        "manuscripts/<int:manuscript_id>/versions/<int:version_id>/export/",
        legacy_views.export_version,
        name="export",
    ),
    path(
        "manuscripts/<int:manuscript_id>/versions/<int:version_id>/promote/",
        legacy_views.promote,
        name="promote",
    ),
]
