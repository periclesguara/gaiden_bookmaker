from django.urls import path

from author_studio import views

app_name = "author_studio"

urlpatterns = [
    path("", views.author_list, name="author_list"),
    path("authors/new/", views.author_create, name="author_create"),
    path("authors/<slug:slug>/", views.author_detail, name="author_detail"),
    path("authors/<slug:slug>/processing/", views.author_processing, name="author_processing"),
    path("authors/<slug:slug>/processing/embeddings/", views.author_embeddings, name="author_embeddings"),
    path("authors/<slug:slug>/split/", views.author_split, name="author_split"),
    path("authors/<slug:author_slug>/works/new/", views.work_create, name="work_create"),
    path("works/<str:code>/", views.work_detail, name="work_detail"),
    path("works/<str:code>/edit/", views.work_edit, name="work_edit"),
    path("works/<str:code>/delete/", views.work_delete, name="work_delete"),
    path("works/<str:code>/split/", views.work_split, name="work_split"),
    path("works/<str:code>/canonical/", views.canonical_text_view, name="canonical_text"),
]
