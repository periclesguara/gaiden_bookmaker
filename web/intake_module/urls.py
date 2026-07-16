from django.urls import path

from . import views

app_name = "intake_module"

urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("new/", views.batch_create, name="batch_create"),
    path("<int:batch_id>/", views.batch_detail, name="batch_detail"),
    path("items/<int:item_id>/", views.item_detail, name="item_detail"),
]
