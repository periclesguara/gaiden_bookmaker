import sqlite3
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse


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
