from django.urls import path

from . import views
from .views_artifacts import artifacts_reindex, artifacts_table

urlpatterns = [
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
        "edition/<int:edition_id>/metadata/",
        views.edition_metadata_edit,
        name="edition_metadata_edit",
    ),
    path(
        "edition/<int:edition_id>/frontispiece/",
        views.frontispiece_preview,
        name="frontispiece_preview",
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
