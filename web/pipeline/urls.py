from django.urls import path
from . import views

urlpatterns = [
    path("", views.pipeline_dashboard, name="pipeline_dashboard"),
    path("jobs/", views.pipeline_jobs, name="pipeline_jobs"),
    path("editions/", views.book_edition_list, name="book_edition_list"),
    path("editions/edit/", views.book_edition_edit, name="book_edition_new"),
    path("editions/edit/<str:book_code>/<str:language>/", views.book_edition_edit, name="book_edition_edit"),
]
