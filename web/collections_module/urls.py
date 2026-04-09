from django.urls import path

from . import views

urlpatterns = [
    path("", views.collection_create, name="collection_create"),
    path("projects/new/", views.project_entry, name="project_entry"),
    path("new/", views.collection_create, name="collection_new"),
    path("<int:collection_id>/items/", views.collection_items, name="collection_items"),
    path("<int:collection_id>/upload/", views.collection_upload, name="collection_upload"),
    path("<int:collection_id>/process/", views.collection_process, name="collection_process"),
    path("<int:collection_id>/review/", views.collection_review, name="collection_review"),
    path("<int:collection_id>/handoff/", views.collection_handoff, name="collection_handoff"),
]
