from django.urls import path
from . import views

urlpatterns = [
    path("", views.pipeline_dashboard, name="pipeline_dashboard"),
    path("jobs/", views.pipeline_jobs, name="pipeline_jobs"),
    path("editions/", views.book_edition_list, name="book_edition_list"),
    path("editions/edit/", views.book_edition_edit, name="book_edition_new"),
    path("editions/edit/<str:book_code>/<str:language>/", views.book_edition_edit, name="book_edition_edit"),
    path("editions/<int:edition_id>/steps/", views.edition_steps, name="edition_steps"),
    path(
        "editions/<int:edition_id>/steps/run/<str:step>/",
        views.run_edition_step,
        name="pipeline_run_edition_step",
    ),
    path(
        "editions/<int:edition_id>/preview-merge-translate/",
        views.preview_merge_translate,
        name="preview_merge_translate",
    ),
    path(
        "editions/<int:edition_id>/save-merge-translate/",
        views.save_merge_translate_preview,
        name="save_merge_translate_preview",
    ),
    path(
        "editions/<str:book_code>/<str:language>/build-md/",
        views.build_book_md,
        name="build_book_md",
    ),
    path(
        "editions/<str:book_code>/<str:language>/preview-md/",
        views.preview_book_md,
        name="preview_book_md",
    ),
    path(
        "editions/<str:book_code>/<str:language>/preview-pre-edition/",
        views.preview_pre_edition_md,
        name="preview_pre_edition_md",
    ),
    path(
        "editions/<str:book_code>/<str:language>/preview-miolo/",
        views.preview_miolo_md,
        name="preview_miolo_md",
    ),
]
