from django.urls import path
from . import views

urlpatterns = [
    path("", views.pipeline_dashboard, name="pipeline_dashboard"),
]
