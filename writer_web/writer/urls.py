from django.urls import path

from writer import views

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
    path(
        "projects/<int:project_id>/supporting-characters/generate/",
        views.generate_supporting_characters,
        name="generate_supporting_characters",
    ),
    path(
        "projects/<int:project_id>/supporting-characters/update/",
        views.update_supporting_characters,
        name="update_supporting_characters",
    ),
    path("projects/<int:project_id>/vectorize/", views.vectorize, name="vectorize"),
    path("projects/<int:project_id>/handoff/", views.export_handoff, name="export_handoff"),
    path("chapters/<int:chapter_id>/", views.chapter_detail, name="chapter_detail"),
    path("chapters/<int:chapter_id>/edit/", views.chapter_edit, name="chapter_edit"),
    path("chapters/<int:chapter_id>/generate/", views.generate, name="generate"),
    path("chapters/<int:chapter_id>/finalize/", views.finalize, name="finalize"),
]
