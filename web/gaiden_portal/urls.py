from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pipeline/", include("pipeline.urls")),
    path("", include("pipeline.urls")),
]
