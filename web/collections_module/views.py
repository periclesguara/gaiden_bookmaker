from django.contrib import messages
from django.contrib.messages import get_messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CollectionCreateForm, CollectionItemForm, CollectionUploadForm, build_collection_item_formset
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
        if "form-TOTAL_FORMS" in request.POST:
            formset = build_collection_item_formset(item_count=max(collection.item_count, 2), data=request.POST)
            form = None
            if formset.is_valid():
                with transaction.atomic():
                    for row_form in formset:
                        data = row_form.cleaned_data
                        CollectionItem.objects.update_or_create(
                            collection=collection,
                            order_index=data["order_index"],
                            defaults={
                                "author_name": data["author_name"],
                                "work_title": data["work_title"],
                                "is_active": True,
                            },
                        )
                    workflow.register_items(collection)
                messages.success(request, "Itens da collection cadastrados com sucesso.")
                return redirect("collection_upload", collection_id=collection.id)
        else:
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
            formset = build_collection_item_formset(
                item_count=max(collection.item_count, 2),
                initial=_collection_item_initial(collection),
            )
    else:
        next_order = collection.items.filter(is_active=True).count() + 1
        form = CollectionItemForm(initial={"order_index": next_order})
        formset = build_collection_item_formset(
            item_count=max(collection.item_count, 2),
            initial=_collection_item_initial(collection),
        )
    items = collection.items.filter(is_active=True).order_by("order_index")
    return render(
        request,
        "collections_module/collection_items.html",
        {"collection": collection, "items": items, "form": form, "formset": formset},
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
            elif action == "pre_images":
                result = workflow.run_pre_images(collection)
                ready = "ready" if result["ready_for_image_maker"] else "needs review"
                messages.success(request, f"Pre-Images package generated ({ready}).")
            elif action == "image_maker_validate":
                workflow.validate_image_maker(collection, request.POST.get("pre_images_package", ""))
                messages.success(request, "Image-Maker rules validated.")
            elif action == "image_maker_build_jobs":
                result = workflow.build_image_maker_jobs(collection, request.POST.get("pre_images_package", ""))
                messages.success(request, f"Image-Maker jobs built: {len(result['jobs'])}.")
            elif action == "image_maker_dry_run":
                result = workflow.dry_run_image_maker(collection)
                messages.success(request, f"Image-Maker dry-run complete: {result['progress']['total_jobs']} jobs.")
            else:
                messages.warning(request, f"Unknown action: {action}")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("collection_process", collection_id=collection.id)
    return render(request, "collections_module/collection_process.html", workflow.build_collection_context(collection))


def collection_review(request, collection_id: int):
    collection = get_object_or_404(Collection, pk=collection_id)
    if collection.pipeline_book_code:
        # Drop stale handoff errors from earlier attempts once the collection has a pipeline target.
        list(get_messages(request))
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


def _collection_item_initial(collection: Collection) -> list[dict]:
    current_items = {item.order_index: item for item in collection.items.filter(is_active=True)}
    total = max(collection.item_count, 2)
    return [
        {
            "order_index": index,
            "author_name": getattr(current_items.get(index), "author_name", ""),
            "work_title": getattr(current_items.get(index), "work_title", ""),
        }
        for index in range(1, total + 1)
    ]
