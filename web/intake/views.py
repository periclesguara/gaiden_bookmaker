from django.shortcuts import render
from django.views.decorators.http import require_GET

from .services.catalog import imported_titles


@require_GET
def home(request):
    return render(request, "intake/home.html", {"books": imported_titles()})
