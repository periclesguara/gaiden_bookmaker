from django.urls import path
from . import views

app_name = "printbook"
urlpatterns = [
    path("", views.home, name="home"),
    path("source/<int:source_id>/", views.source_dashboard, name="source"),
    path("source/<int:source_id>/preview/", views.source_preview, name="source_preview"),
    path("source/<int:source_id>/assets/<path:member>", views.source_asset, name="source_asset"),
    path("<int:pk>/cover/", views.cover_dashboard, name="cover"),
    path("<int:pk>/book-preview/", views.book_preview, name="book_preview"),
    path("cover-image/<int:pk>/", views.cover_source_image, name="cover_source_image"),
    path("cover-template/<int:pk>/", views.cover_template, name="cover_template"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/action/<str:action>/", views.action, name="action"),
    path("artifact/<str:kind>/<int:pk>/", views.download, name="artifact"),
    path("preview/<str:kind>/<int:pk>/", views.preview, name="preview"),
    path("preview/<str:kind>/<int:pk>/<int:page>.png", views.page_image, name="page"),
]
