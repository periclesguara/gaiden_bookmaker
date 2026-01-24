from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("pipeline/", include("pipeline.urls")),
    path("editorial/", include("editorial.urls")),
    path("go/", RedirectView.as_view(url="/editorial/organizer/", permanent=False)),
    path("", include("pipeline.urls")),
]
