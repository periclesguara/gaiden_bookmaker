from django.urls import path

from . import views

app_name = "intake_module"

urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("new/", views.batch_create, name="batch_create"),
    path("batches/<int:batch_id>/files/", views.batch_files, name="batch_files"),
    path(
        "batches/<int:batch_id>/files/import/",
        views.batch_import_selected,
        name="batch_import_selected",
    ),
    path("<int:batch_id>/", views.batch_detail, name="batch_detail"),
    path("<int:batch_id>/upload/", views.batch_upload, name="batch_upload"),
    path("<int:batch_id>/drive/", views.batch_drive, name="batch_drive"),
    path("<int:batch_id>/process-next/", views.batch_process_next, name="batch_process_next"),
    path("items/<int:item_id>/", views.item_detail, name="item_detail"),
    path("items/<int:item_id>/metadata/", views.item_update_metadata, name="item_update_metadata"),
    path("items/<int:item_id>/download/", views.item_download, name="item_download"),
    path("items/<int:item_id>/clean/", views.item_clean, name="item_clean"),
    path("items/<int:item_id>/prepare-codex/", views.item_prepare_codex, name="item_prepare_codex"),
    path("items/<int:item_id>/register-return/", views.item_register_return, name="item_register_return"),
    path("items/<int:item_id>/confirm-ready/", views.item_confirm_ready, name="item_confirm_ready"),
    path("items/<int:item_id>/handoff/", views.item_handoff, name="item_handoff"),
]
