from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from gaiden.application.author_studio.ingest_work_source import ingest_new_work
from gaiden.application.author_studio.delete_work import delete_work
from gaiden.application.author_studio.update_work import update_work
from gaiden.domain.author_studio.exceptions import AuthorStudioError

from .forms import AuthorCreateForm, WorkCreateForm, WorkEditForm
from .models import Author, CanonicalText, Work


def author_list(request):
    authors = Author.objects.annotate(work_count=Count("works")).order_by("name")
    return render(request, "author_studio/author_list.html", {"authors": authors})


def author_create(request):
    form = AuthorCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            author = form.save()
        except Exception as exc:
            form.add_error("name", exc)
        else:
            messages.success(request, f"Autor {author.code} criado com sucesso.")
            return redirect("author_studio:author_detail", slug=author.slug)
    return render(request, "author_studio/author_form.html", {"form": form})


def author_detail(request, slug):
    author = get_object_or_404(Author.objects.prefetch_related("works"), slug=slug)
    return render(request, "author_studio/author_detail.html", {"author": author})


def work_create(request, author_slug):
    author = get_object_or_404(Author, slug=author_slug)
    form = WorkCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = ingest_new_work(
                author=author,
                title=form.cleaned_data["title"],
                original_language=form.cleaned_data["original_language"],
                upload=form.cleaned_data["source_file"],
            )
            work = result.work
        except AuthorStudioError as exc:
            form.add_error(None, str(exc))
        except Exception as exc:
            form.add_error(None, f"Não foi possível adicionar a obra: {exc}")
        else:
            messages.success(request, f"Obra {work.code} adicionada com sucesso.")
            return redirect("author_studio:work_detail", code=work.code)
    return render(request, "author_studio/work_form.html", {"author": author, "form": form})


def work_detail(request, code):
    work = get_object_or_404(Work.objects.select_related("author").prefetch_related("sources"), code=code)
    canonical = CanonicalText.objects.filter(work=work).first()
    return render(request, "author_studio/work_detail.html", {"work": work, "canonical": canonical})


def work_edit(request, code):
    work = get_object_or_404(Work.objects.select_related("author"), code=code)
    form = WorkEditForm(
        request.POST or None,
        initial={"title": work.title, "original_language": work.original_language},
    )
    if request.method == "POST" and form.is_valid():
        try:
            work = update_work(work=work, **form.cleaned_data)
        except AuthorStudioError as exc:
            form.add_error("title", str(exc))
        else:
            messages.success(request, f"Obra {work.code} atualizada com sucesso.")
            return redirect("author_studio:work_detail", code=work.code)
    return render(request, "author_studio/work_edit.html", {"work": work, "form": form})


def work_delete(request, code):
    work = get_object_or_404(Work.objects.select_related("author"), code=code)
    if request.method != "POST":
        return redirect("author_studio:work_detail", code=work.code)
    author_slug, deleted_code, deleted_title = delete_work(work=work)
    messages.success(request, f"Obra {deleted_code} — {deleted_title} excluída.")
    return redirect("author_studio:author_detail", slug=author_slug)


def canonical_text_view(request, code):
    canonical = get_object_or_404(CanonicalText.objects.select_related("work", "work__author"), work__code=code)
    with canonical.text_file.open("r") as handle:
        text = handle.read()
    return render(request, "author_studio/canonical_text.html", {"canonical": canonical, "text": text})
