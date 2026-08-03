from django.contrib import admin
from django.urls import include, path

from collections_module import views as collection_views
from pipeline import views_incremental

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", collection_views.project_entry, name="root"),
    path("collections/", include("collections_module.urls")),
    path("writer/", include("web.writer.urls")),
    path("intake/", include("web.intake.urls")),
    path("manual/", include("web.manual_ai.urls")),
    path(
        "intake/automated/",
        views_incremental.automated_editorial_import_dashboard,
        name="automated_editorial_import",
    ),
    path(
        "intake/automated/import/preview/",
        views_incremental.automated_editorial_import_preview,
        name="automated_editorial_import_preview",
    ),
    path(
        "intake/automated/import/confirm/",
        views_incremental.automated_editorial_import_confirm,
        name="automated_editorial_import_confirm",
    ),
    path(
        "intake/automated/drive/browse/",
        views_incremental.automated_drive_browse,
        name="automated_drive_browse",
    ),
    path(
        "intake/automated/drive/folder/preview/",
        views_incremental.automated_drive_folder_preview,
        name="automated_drive_folder_preview",
    ),
    path(
        "intake/automated/drive/folder/confirm/",
        views_incremental.automated_drive_folder_confirm,
        name="automated_drive_folder_confirm",
    ),
    path(
        "intake/automated/import/retry/",
        views_incremental.automated_drive_import_retry,
        name="automated_drive_import_retry",
    ),
    path("pipeline/", include("pipeline.urls")),
    path("editorial/", include("editorial.urls")),
    path("author-studio/", include("web.author_studio.urls")),
    path("", include("pipeline.urls")),
]
