from django.shortcuts import render

from gaiden.application.dashboard.home import home_projection


def home(request):
    return render(request, "gaiden_portal/home.html", home_projection())
