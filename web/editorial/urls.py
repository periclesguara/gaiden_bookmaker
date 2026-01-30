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
        "frontmatter/<int:edition_id>/block/<str:block_type>/save/",
        views.save_block,
        name="save_block",
    ),
    path(
        "frontmatter/<int:edition_id>/block/<str:block_type>/clear/",
        views.clear_block,
        name="clear_block",
    ),
    path(
        "frontmatter/<int:edition_id>/block/<str:block_type>/toggle-lock/",
        views.toggle_block_lock,
        name="toggle_block_lock",
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
        "organizer/",
        views.organizer_home,
        name="organizer_home",
    ),
    path(
        "organizer/open/",
        views.organizer_open,
        name="organizer_open",
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
