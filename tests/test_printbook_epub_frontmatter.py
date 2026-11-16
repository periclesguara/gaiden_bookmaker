import zipfile

from gaiden.print_engine.epub_frontmatter import merge_frontmatter_with_body


def _xhtml(title, body, epub_type="frontmatter"):
    return f'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{title}</title></head><body epub:type="{epub_type}"><section><h1>{title}</h1>{body}</section></body></html>'''


def test_epub_frontmatter_follows_spine_and_does_not_duplicate_body(tmp_path):
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="EPUB/content.opf"/></rootfiles></container>')
        archive.writestr("EPUB/content.opf", '''<package><manifest>
          <item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>
          <item id="title" href="text/title.xhtml" media-type="application/xhtml+xml"/>
          <item id="intro" href="text/introduction.xhtml" media-type="application/xhtml+xml"/>
          <item id="chapter" href="text/chapter_001.xhtml" media-type="application/xhtml+xml"/>
        </manifest><spine><itemref idref="cover"/><itemref idref="title"/><itemref idref="intro"/><itemref idref="chapter"/></spine></package>''')
        archive.writestr("EPUB/text/cover.xhtml", _xhtml("Capa", ""))
        archive.writestr("EPUB/text/title.xhtml", _xhtml("Folha de rosto", "<p>Autor</p>"))
        archive.writestr("EPUB/text/introduction.xhtml", _xhtml("Introdução", "<p>Texto aprovado.</p>"))
        archive.writestr("EPUB/text/chapter_001.xhtml", _xhtml("Capítulo", "<p>Não importar.</p>", "bodymatter"))

    body = "# Título mínimo\n\n\\newpage\n\n# Capitulo 01 - Corpo\n\nTexto do miolo."
    merged = merge_frontmatter_with_body(epub, body)

    assert "# Folha de rosto" in merged
    assert "# Introdução" in merged
    assert "Texto aprovado." in merged
    assert "# Título mínimo" not in merged
    assert "Não importar." not in merged
    assert merged.count("# Capitulo 01 - Corpo") == 1
