from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CollectionCreateForm, CollectionItemForm, CollectionUploadForm
from .models import Collection, CollectionItem
from .services import workflow


def project_entry(request):
    return render(request, "collections_module/project_entry.html")


def collection_create(request):
    if request.method == "POST":
        form = CollectionCreateForm(request.POST)
        if form.is_valid():
            collection = workflow.create_collection(**form.cleaned_data)
            return redirect("collection_items", collection_id=collection.id)
    else:
        form = CollectionCreateForm(initial={"item_count": 2, "language": "en"})
    return render(request, "collections_module/collection_form.html", {"form": form})


def collection_items(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    if request.method == "POST":
        current_count = collection.items.filter(is_active=True).count()
        if current_count >= max(collection.item_count, 2):
            messages.error(request, "Collection item limit reached.")
            return redirect("collection_items", collection_id=collection.id)
        form = CollectionItemForm(request.POST)
        if form.is_valid():
            expected_order = current_count + 1
            if form.cleaned_data["order_index"] != expected_order:
                form.add_error("order_index", f"Next item must use contiguous order {expected_order}.")
            elif collection.items.filter(
                is_active=True,
                author_name=form.cleaned_data["author_name"],
                work_title=form.cleaned_data["work_title"],
            ).exists():
                form.add_error("work_title", "Duplicate item in the same collection is not allowed.")
        if form.is_valid():
            item = form.save(commit=False)
            item.collection = collection
            item.save()
            workflow.register_items(collection)
            messages.success(request, "Collection item saved.")
            return redirect("collection_items", collection_id=collection.id)
    else:
        next_order = collection.items.filter(is_active=True).count() + 1
        form = CollectionItemForm(initial={"order_index": next_order})
    items = collection.items.filter(is_active=True).order_by("order_index")
    return render(
        request,
        "collections_module/collection_items.html",
        {"collection": collection, "items": items, "form": form},
    )


def collection_upload(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    if request.method == "POST":
        form = CollectionUploadForm(request.POST, request.FILES)
        if form.is_valid():
            item = get_object_or_404(CollectionItem, pk=form.cleaned_data["item_id"], collection=collection)
            workflow.store_collection_upload(collection, item, form.cleaned_data["source_file"])
            messages.success(request, f"Upload received for item {item.order_index}.")
            return redirect("collection_upload", collection_id=collection.id)
    else:
        form = CollectionUploadForm()
    items = collection.items.filter(is_active=True).order_by("order_index")
    return render(
        request,
        "collections_module/collection_upload.html",
        {"collection": collection, "items": items, "form": form},
    )


def collection_process(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "prepare":
                workflow.run_prepare(collection)
                messages.success(request, "Collection preparation completed.")
            elif action == "normalize":
                workflow.run_normalize(collection)
                messages.success(request, "Collection normalize completed.")
            elif action == "merge":
                workflow.run_merge(collection)
                messages.success(request, "Collection merge completed.")
            else:
                messages.warning(request, f"Unknown action: {action}")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("collection_process", collection_id=collection.id)
    return render(request, "collections_module/collection_process.html", workflow.build_collection_context(collection))


def collection_review(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    return render(
        request,
        "collections_module/collection_review.html",
        {**workflow.build_collection_context(collection), "preview": workflow.merged_preview(collection)},
    )


def collection_handoff(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    if request.method != "POST":
        return redirect("collection_review", collection_id=collection.id)
    try:
        edition = workflow.handoff_to_pipeline(collection)
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("collection_review", collection_id=collection.id)
    messages.success(request, "Collection handed off to the standard pipeline.")
    return redirect("edition_steps", edition_id=edition.id)
