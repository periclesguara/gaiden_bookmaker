from django.urls import path

from . import views

app_name = "finalized_projects"
urlpatterns = [path("", views.list_projects, name="list")]
