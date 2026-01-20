from django.urls import path

from . import views

urlpatterns = [
    path(
        "frontmatter/<str:book_code>/<str:language>/",
        views.frontmatter_template_edit,
        name="frontmatter_template_edit",
    ),
    path(
        "edition/<int:edition_id>/edit/",
        views.edition_edit,
        name="edition_edit",
    ),
    path(
        "edition/<int:edition_id>/frontispiece/",
        views.frontispiece_preview,
        name="frontispiece_preview",
    ),
]
