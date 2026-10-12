from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from gaiden.application.author_studio.ingest_work_source import ingest_new_work
from gaiden.application.author_studio.delete_work import delete_work
from gaiden.application.author_studio.split_work import split_author_works, split_work
from gaiden.application.author_studio.update_work import update_work
from gaiden.domain.author_studio.enums import SplitStatus
from gaiden.domain.author_studio.exceptions import AuthorStudioError

from .forms import AuthorCreateForm, WorkCreateForm, WorkEditForm
from .models import Author, CanonicalText, Work


def _processing_row(work):
    try:
        canonical = work.canonical_text
    except CanonicalText.DoesNotExist:
        canonical = None
    try:
        split_run = work.split_run
    except Exception:
        split_run = None
    split_complete = bool(
        canonical
        and split_run
        and split_run.status == SplitStatus.COMPLETED.value
        and split_run.source_sha256 == canonical.sha256
    )
    return {
        "work": work,
        "canonical": canonical,
        "split_run": split_run,
        "split_complete": split_complete,
        "chunk_count": split_run.chunk_count if split_complete else 0,
    }


def _author_processing_context(author):
    work_rows = [_processing_row(work) for work in author.works.all()]
    completed_rows = [row for row in work_rows if row["split_complete"]]
    return {
        "author": author,
        "work_rows": work_rows,
        "all_split_complete": bool(work_rows) and len(completed_rows) == len(work_rows),
        "completed_work_count": len(completed_rows),
        "total_chunks": sum(row["chunk_count"] for row in completed_rows),
    }


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
    author = get_object_or_404(
        Author.objects.prefetch_related("works__canonical_text", "works__split_run"),
        slug=slug,
    )
    return render(request, "author_studio/author_detail.html", _author_processing_context(author))


def author_processing(request, slug):
    author = get_object_or_404(
        Author.objects.prefetch_related("works__canonical_text", "works__split_run"),
        slug=slug,
    )
    return render(request, "author_studio/processing.html", _author_processing_context(author))


def author_embeddings(request, slug):
    author = get_object_or_404(
        Author.objects.prefetch_related("works__canonical_text", "works__split_run"),
        slug=slug,
    )
    context = _author_processing_context(author)
    if not context["all_split_complete"]:
        messages.warning(request, "Conclua a etapa 01 de todas as obras antes de prosseguir.")
        return redirect("author_studio:author_processing", slug=author.slug)
    return render(request, "author_studio/embeddings_setup.html", context)


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
    work = get_object_or_404(
        Work.objects.select_related("author").prefetch_related("sources"),
        code=code,
    )
    canonical = CanonicalText.objects.filter(work=work).first()
    processing = _processing_row(work)
    return render(request, "author_studio/work_detail.html", {"work": work, "canonical": canonical, "processing": processing})


def author_split(request, slug):
    author = get_object_or_404(Author, slug=slug)
    if request.method != "POST":
        return redirect("author_studio:author_detail", slug=author.slug)
    results, errors = split_author_works(author=author)
    if results:
        total_chunks = sum(item.chunk_count for item in results)
        messages.success(request, f"Etapa 01 concluída: {len(results)} obras, {total_chunks} chunks.")
    for code, error in errors:
        messages.error(request, f"{code}: {error}")
    return redirect("author_studio:author_detail", slug=author.slug)


def work_split(request, code):
    work = get_object_or_404(Work.objects.select_related("author"), code=code)
    if request.method != "POST":
        return redirect("author_studio:work_detail", code=work.code)
    try:
        result = split_work(work=work)
    except Exception as exc:
        messages.error(request, f"Não foi possível executar o split: {exc}")
    else:
        messages.success(request, f"Etapa 01 concluída: {result.chunk_count} chunks.")
    return redirect("author_studio:work_detail", code=work.code)


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
