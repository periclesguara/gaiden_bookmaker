from django.urls import path

from . import views
from .views_artifacts import artifact_preview, artifacts_reindex, artifacts_table

urlpatterns = [
    path(
        "edition/<int:edition_id>/epub-preview/",
        views.premium_epub_preview,
        name="premium_epub_preview",
    ),
    path(
        "edition/<int:edition_id>/epub-preview/approve/",
        views.premium_epub_approve,
        name="premium_epub_approve",
    ),
    path(
        "edition/<int:edition_id>/epub-preview/artifact/<path:relative_path>",
        views.premium_epub_asset,
        name="premium_epub_asset",
    ),
    path(
        "frontmatter/<str:book_code>/<str:language>/",
        views.frontmatter_template_edit,
        name="frontmatter_template_edit",
    ),
    path(
        "edition/<int:edition_id>/frontmatter-actions/",
        views.editorial_frontmatter_actions,
        name="editorial_frontmatter_actions",
    ),
    path(
        "edition/<int:edition_id>/toggle-stage-lock/",
        views.toggle_stage_lock,
        name="toggle_stage_lock",
    ),
    path(
        "edition/<int:edition_id>/edit/",
        views.edition_edit,
        name="edition_edit",
    ),
    path(
        "edition/<int:edition_id>/frontispiece/",
        views.frontispiece_preview,
        name="frontispiece_preview",
    ),
    path(
        "artifacts/file/<int:artifact_id>/",
        artifact_preview,
        name="artifact_preview",
    ),
    path(
        "artifacts/<str:work_code>/<str:lang>/",
        artifacts_table,
        name="artifacts_table",
    ),
    path(
        "artifacts/<str:work_code>/reindex/",
        artifacts_reindex,
        name="artifacts_reindex",
    ),
]
