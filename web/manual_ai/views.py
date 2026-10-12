from django.shortcuts import redirect, render


def home(request):
    return render(request, "manual_ai/home.html")


def edition(request, edition_id):
    return redirect("post_intake_workflow", edition_id=edition_id)


def stage(request, stage):
    return render(request, "manual_ai/stage.html", {"stage": stage.title()})
