from django.urls import path
from . import views

app_name = "corpus"
urlpatterns = [path("", views.catalog, name="catalog"),
               path("projects/<int:project_id>/", views.project_selection, name="project")]
