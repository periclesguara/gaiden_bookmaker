from django.contrib import admin
from django.urls import path

from pipeline.views import pipeline_dashboard

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pipeline/", pipeline_dashboard, name="pipeline_dashboard"),
]
