from django.urls import path
from . import views

urlpatterns = [
    path("", views.pipeline_dashboard, name="pipeline_dashboard"),
    path("dashboard/", views.pipeline_project_dashboard, name="pipeline_project_dashboard"),
    path("projects/", views.projects_list, name="projects_list"),
    path("projects/new/", views.projects_new, name="projects_new"),
    path("projects/<str:book_code>/", views.projects_hub, name="projects_hub"),
    path("projects/<str:book_code>/edit/", views.projects_edit, name="projects_edit"),
    path("projects/<str:book_code>/upload/<str:language>/", views.projects_upload_raw, name="projects_upload_raw"),
    path(
        "projects/<str:book_code>/normalize-preview/",
        views.projects_normalize_preview,
        name="projects_normalize_preview",
    ),
    path(
        "projects/<str:book_code>/normalize-preview/<str:language>/",
        views.projects_normalize_preview,
        name="projects_normalize_preview_lang",
    ),
    path(
        "normalize/preview/<str:book_code>/<str:language>/",
        views.projects_normalize_preview,
        name="normalize_preview",
    ),
    path(
        "chunks/manifest/<str:book_code>/<str:language>/",
        views.projects_chunks_manifest,
        name="projects_chunks_manifest",
    ),
    path("jobs/", views.pipeline_jobs, name="pipeline_jobs"),
    path("runner/", views.runner_matrix_view, name="pipeline_runner_matrix"),
    path("runner/run/", views.runner_matrix_run_view, name="pipeline_runner_run"),
    path("runner/<int:run_id>/", views.runner_matrix_detail_view, name="pipeline_runner_matrix_detail"),
    path("translate/", views.translate_control, name="pipeline_translate_control"),
    path("editions/", views.book_edition_list, name="book_edition_list"),
    path("editions/edit/", views.book_edition_edit, name="book_edition_new"),
    path("editions/edit/<str:book_code>/<str:language>/", views.book_edition_edit, name="book_edition_edit"),
    path("editions/<int:edition_id>/steps/", views.edition_steps, name="edition_steps"),
    path("editions/steps/<str:book_code>/<str:language>/", views.edition_steps_by_code, name="edition_steps_by_code"),
    path(
        "editions/<str:book_code>/<str:language>/build-md/",
        views.build_book_md,
        name="build_book_md",
    ),
    path(
        "<str:book_code>/fasttrack_text_to_md/",
        views.fasttrack_text_to_md,
        name="pipeline_fasttrack_text_to_md",
    ),
]
