from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pipeline/", include("pipeline.urls")),
    path("editorial/", include("editorial.urls")),
    path("", include("pipeline.urls")),
]
