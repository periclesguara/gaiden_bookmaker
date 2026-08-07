from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [path("", views.home, name="home")]
