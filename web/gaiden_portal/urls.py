from django.contrib import admin
from django.urls import include, path

from collections_module import views as collection_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", collection_views.project_entry, name="root"),
    path("collections/", include("collections_module.urls")),
    path("pipeline/", include("pipeline.urls")),
    path("editorial/", include("editorial.urls")),
    path("", include("pipeline.urls")),
]
