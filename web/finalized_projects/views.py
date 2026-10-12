from django.shortcuts import render

from gaiden.application.builds.finalized_projects import finalized_project_builds


def list_projects(request):
    return render(
        request,
        "finalized_projects/list.html",
        {"builds": finalized_project_builds()},
    )
