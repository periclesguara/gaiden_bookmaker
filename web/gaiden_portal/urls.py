from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pipeline/", include("pipeline.urls")),
    path("editorial/", include("editorial.urls")),
    path("", RedirectView.as_view(pattern_name="book_edition_new", permanent=False), name="root"),
    path("", include("pipeline.urls")),
]
