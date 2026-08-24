from django.urls import path
from . import views
from . import views_incremental

urlpatterns = [
    path("", views.pipeline_dashboard, name="pipeline_dashboard"),
    path(
        "incremental-import/",
        views_incremental.incremental_import_dashboard,
        name="pipeline_incremental_import",
    ),
    path("html/<int:edition_id>/", views.pipeline_html_dashboard, name="pipeline_html_dashboard"),
    path(
        "html/<int:edition_id>/reupload/run/",
        views.pipeline_html_reupload_run,
        name="pipeline_html_reupload_run",
    ),
    path(
        "html/<int:edition_id>/preprod/run/",
        views.pipeline_html_preprod_run,
        name="pipeline_html_preprod_run",
    ),
    path(
        "html/<int:edition_id>/convert/run/",
        views.pipeline_html_convert_run,
        name="pipeline_html_convert_run",
    ),
    path(
        "html/<int:edition_id>/md_normalize/run/",
        views.pipeline_html_md_normalize_run,
        name="pipeline_html_md_normalize_run",
    ),
    path("jobs/", views.pipeline_jobs, name="pipeline_jobs"),
    path("editions/", views.book_edition_list, name="book_edition_list"),
    path("editions/imported/", views.imported_book_list, name="imported_book_list"),
    path(
        "editions/imported/<int:item_id>/preview/",
        views.imported_book_preview,
        name="imported_book_preview",
    ),
    path("editions/dashboard/", views.production_dashboard, name="production_dashboard"),
    path("builds/<int:build_id>/", views.final_build_detail, name="final_build_detail"),
    path("builds/<int:build_id>/download/", views.download_final_build, name="final_build_download"),
    path("builds/<int:build_id>/outdated/", views.mark_final_build_outdated, name="final_build_mark_outdated"),
    path(
        "editions/imported/<int:item_id>/select/",
        views.imported_book_select,
        name="imported_book_select",
    ),
    path(
        "editions/<int:edition_id>/production/",
        views.post_intake_workflow,
        name="post_intake_workflow",
    ),
    path(
        "editions/<int:edition_id>/production/save/",
        views.save_production_bookmark,
        name="save_production_bookmark",
    ),
    path(
        "editions/<int:edition_id>/production/google-drive/export/",
        views.manual_translation_export,
        name="manual_translation_export",
    ),
    path(
        "translation-jobs/<int:job_id>/import-drive/",
        views.manual_translation_import_drive,
        name="manual_translation_import_drive",
    ),
    path(
        "translation-jobs/<int:job_id>/import-upload/",
        views.manual_translation_import_upload,
        name="manual_translation_import_upload",
    ),
    path("editions/edit/", views.book_edition_edit, name="book_edition_new"),
    path("editions/edit/<str:book_code>/<str:language>/", views.book_edition_edit, name="book_edition_edit"),
    path(
        "editions/upload/<str:book_code>/<str:language>/",
        views.book_edition_upload,
        name="book_edition_upload",
    ),
    path("editions/<int:edition_id>/steps/", views.edition_steps, name="edition_steps"),
    path(
        "editions/<int:edition_id>/normalize/run/",
        views.pipeline_normalize_run,
        name="pipeline_normalize_run",
    ),
    path(
        "editions/<int:edition_id>/chunk/run/",
        views.pipeline_chunk_run,
        name="pipeline_chunk_run",
    ),
    path(
        "editions/<int:edition_id>/heading_cleaner/run/",
        views.pipeline_heading_cleaner_run,
        name="pipeline_heading_cleaner_run",
    ),
    path(
        "editions/<int:edition_id>/translate/run/",
        views.pipeline_translate_run,
        name="pipeline_translate_run",
    ),
    path(
        "editions/<int:edition_id>/refine/run/",
        views.pipeline_refine_run,
        name="pipeline_refine_run",
    ),
    path(
        "editions/<int:edition_id>/merge_refine/run/",
        views.pipeline_merge_refine_run,
        name="pipeline_merge_refine_run",
    ),
    path(
        "editions/<int:edition_id>/preflight/run/",
        views.pipeline_preflight_run,
        name="pipeline_preflight_run",
    ),
    path(
        "editions/<int:edition_id>/steps/run/<str:step>/",
        views.run_edition_step,
        name="pipeline_run_edition_step",
    ),
    path(
        "editions/<int:edition_id>/download-last-version/",
        views.download_last_version,
        name="pipeline_download_last_version",
    ),
    path(
        "editions/<int:edition_id>/cover/",
        views.edition_cover_file,
        name="pipeline_edition_cover",
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
        "editions/<int:edition_id>/preview-merge-polidor/",
        views.preview_merge_polidor,
        name="preview_merge_polidor",
    ),
    path(
        "editions/<int:edition_id>/preview-merge/",
        views.preview_merge_selected,
        name="preview_merge_selected",
    ),
    path(
        "editions/<int:edition_id>/save-merge-polidor/",
        views.save_merge_polidor_preview,
        name="save_merge_polidor_preview",
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
