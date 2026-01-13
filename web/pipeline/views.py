import sqlite3
import os
import zipfile
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from pathlib import Path

from django.conf import settings
from django.db.models import Case, Count, IntegerField, Q, When
from django.shortcuts import redirect, render

from .forms import BookEditionTemplateForm
from .models import BookEditionTemplate, PipelineJob


DB_PATH = Path(settings.BASE_DIR).parent / "data" / "db" / "gaiden.sqlite3"


def _badge(flag: bool) -> str:
    return "✅" if flag else "—"


def pipeline_dashboard(request):
    # Conecta no SQLite do Gaiden
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Em vez de depender da tabela books, usamos o que EXISTE:
    # os book_id reais da book_translated_merged
    try:
        cur.execute(
            """
            SELECT DISTINCT book_id AS id
              FROM book_translated_merged
             ORDER BY book_id;
            """
        )
        books = cur.fetchall()
    except sqlite3.Error as e:
        conn.close()
        return HttpResponse(
            f"<h1>Gaiden Pipeline</h1><p>Erro acessando DB: {e}</p>",
            content_type="text/html",
        )

    books_data = []

    for book in books:
        book_id = book["id"]

        # translated
        cur.execute(
            """
            SELECT lang_key
              FROM book_translated_merged
             WHERE book_id = ?
            """,
            (book_id,),
        )
        translated_langs = [row["lang_key"] for row in cur.fetchall()]

        # refined
        try:
            cur.execute(
                """
                SELECT lang, variant
                  FROM book_refined_merged
                 WHERE book_id = ?
                """,
                (book_id,),
            )
            refined_rows = cur.fetchall()
        except sqlite3.OperationalError:
            refined_rows = []

        refined_langs = [row["lang"] for row in refined_rows]

        # polished
        try:
            cur.execute(
                """
                SELECT lang, variant
                  FROM book_polished_merged
                 WHERE book_id = ?
                """,
                (book_id,),
            )
            polished_rows = cur.fetchall()
        except sqlite3.OperationalError:
            polished_rows = []

        polished_langs = [row["lang"] for row in polished_rows]

        all_langs = sorted(set(translated_langs) | set(refined_langs) | set(polished_langs))

        lang_status = []
        for lang in all_langs:
            lang_status.append(
                {
                    "lang": lang,
                    "translated": lang in translated_langs,
                    "refined": lang in refined_langs,
                    "polished": lang in polished_langs,
                }
            )

        books_data.append(
            {
                "id": book_id,
                "label": f"Book {book_id}",
                "langs": lang_status,
            }
        )

    conn.close()

    parts = []
    parts.append("<html><head><meta charset='utf-8'><title>Gaiden Pipeline</title></head><body>")
    parts.append("<h1>Gaiden Pipeline – Status dos Livros</h1>")

    if not books_data:
        parts.append("<p>Nenhum livro encontrado em <code>book_translated_merged</code>.</p>")
    else:
        for book in books_data:
            parts.append(
                f"<h2>Livro {book['id']}: {book['label']}</h2>"
            )
            parts.append(
                "<table border='1' cellspacing='0' cellpadding='4'>"
                "<tr>"
                "<th>Idioma</th>"
                "<th>Translated</th>"
                "<th>Refined</th>"
                "<th>Polished</th>"
                "</tr>"
            )
            for ls in book["langs"]:
                parts.append(
                    "<tr>"
                    f"<td>{ls['lang']}</td>"
                    f"<td style='text-align:center'>{_badge(ls['translated'])}</td>"
                    f"<td style='text-align:center'>{_badge(ls['refined'])}</td>"
                    f"<td style='text-align:center'>{_badge(ls['polished'])}</td>"
                    "</tr>"
                )
            parts.append("</table>")

    parts.append("</body></html>")

    return HttpResponse("\n".join(parts), content_type="text/html")


def pipeline_dashboard(request):
    books = (
        PipelineJob.objects.values("book_code", "book_title")
        .annotate(
            total_jobs=Count("id"),
            success_jobs=Count("id", filter=Q(status="SUCCESS")),
            fail_jobs=Count("id", filter=Q(status="FAIL")),
        )
        .order_by("book_code")
    )
    return render(request, "pipeline/dashboard.html", {"books": books})


def pipeline_jobs(request):
    stage_order = Case(
        When(stage="raw", then=0),
        When(stage="normalize", then=1),
        When(stage="split", then=2),
        When(stage="translate", then=3),
        When(stage="refine", then=4),
        When(stage="polish", then=5),
        default=99,
        output_field=IntegerField(),
    )

    qs = (
        PipelineJob.objects.annotate(stage_index=stage_order)
        .order_by("book_code", "language", "stage_index")
    )
    book_code = request.GET.get("book")
    if book_code:
        qs = qs.filter(book_code=book_code)
    return render(request, "pipeline/jobs.html", {"jobs": qs})


def book_edition_list(request):
    editions = BookEditionTemplate.objects.all().order_by("book_code", "language")
    return render(request, "pipeline/book_edition_list.html", {"editions": editions})


def book_edition_edit(request, book_code=None, language=None):
    instance = None

    if book_code and language:
        try:
            instance = BookEditionTemplate.objects.get(book_code=book_code, language=language)
        except BookEditionTemplate.DoesNotExist:
            instance = None

    if request.method == "POST":
        form = BookEditionTemplateForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            project_root = Path(settings.BASE_DIR).parent
            data_dir = project_root / "data"

            cover_file = form.cleaned_data.get("cover_file")
            if cover_file:
                cover_dir = data_dir / "covers" / obj.book_code / obj.language
                cover_dir.mkdir(parents=True, exist_ok=True)
                cover_path = cover_dir / cover_file.name
                with cover_path.open("wb+") as dest:
                    for chunk in cover_file.chunks():
                        dest.write(chunk)
                obj.cover_filepath = os.path.relpath(cover_path, project_root)

            images_zip = form.cleaned_data.get("images_zip")
            if images_zip:
                images_dir = data_dir / "images" / obj.book_code / obj.language
                images_dir.mkdir(parents=True, exist_ok=True)
                zip_path = images_dir / images_zip.name
                with zip_path.open("wb+") as dest:
                    for chunk in images_zip.chunks():
                        dest.write(chunk)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(images_dir)
                try:
                    zip_path.unlink()
                except FileNotFoundError:
                    pass
                obj.images_dir = os.path.relpath(images_dir, project_root)

            obj.save()
            uploaded = form.cleaned_data.get("source_file")
            if uploaded:
                raw_base_dir = data_dir / "raw"
                dest_dir = raw_base_dir / obj.book_code
                dest_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(uploaded.name).suffix or ".txt"
                dest_path = dest_dir / f"{obj.book_code}_{obj.language}_raw{ext}"
                with dest_path.open("wb+") as dest:
                    for chunk in uploaded.chunks():
                        dest.write(chunk)
                PipelineJob.objects.update_or_create(
                    book_code=obj.book_code,
                    language=obj.language,
                    stage="raw",
                    defaults={
                        "book_title": obj.title,
                        "status": "SUCCESS",
                        "filepath": str(dest_path),
                        "message": "Raw file uploaded.",
                    },
                )
            return redirect("book_edition_edit", book_code=obj.book_code, language=obj.language)
    else:
        initial = {}
        if book_code:
            initial["book_code"] = book_code
        if language:
            initial["language"] = language
        form = BookEditionTemplateForm(instance=instance, initial=initial)

    return render(
        request,
        "pipeline/book_edition_form.html",
        {"form": form, "instance": instance},
    )
