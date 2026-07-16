from django.shortcuts import get_object_or_404, redirect, render

from .forms import IntakeBatchForm, IntakeUploadForm
from .models import IntakeBatch, IntakeItem


def batch_list(request):
    return render(request, "intake_module/batch_list.html", {"batches": IntakeBatch.objects.all()})


def batch_create(request):
    form = IntakeBatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        batch = form.save()
        return redirect("intake_module:batch_detail", batch_id=batch.id)
    return render(request, "intake_module/batch_form.html", {"form": form})


def batch_detail(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    return render(
        request,
        "intake_module/batch_detail.html",
        {"batch": batch, "items": batch.items.all(), "upload_form": IntakeUploadForm()},
    )


def item_detail(request, item_id: int):
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    return render(request, "intake_module/item_detail.html", {"item": item})
