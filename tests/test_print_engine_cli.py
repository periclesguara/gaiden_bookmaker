from __future__ import annotations

import ast
import json
from pathlib import Path

from gaiden.print_engine import cli
from gaiden.print_engine.compiler import LatexCompileResult


def _book_payload() -> dict[str, object]:
    return {
        "title": "Approved Book",
        "author": "Approved Author",
        "language": "pt-BR",
        "chapters": [{"title": "Chapter 1", "body": "Approved body."}],
    }


def test_load_book_content_reads_approved_json_without_application_dependencies(tmp_path):
    source = tmp_path / "book.json"
    source.write_text(json.dumps(_book_payload()), encoding="utf-8")

    content = cli.load_book_content(source)

    assert content.title == "Approved Book"
    assert content.chapters[0].body == "Approved body."


def test_build_interior_command_writes_tex_compiles_and_reports_pages(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "book.json"
    source.write_text(json.dumps(_book_payload()), encoding="utf-8")
    output = tmp_path / "print"

    def fake_compile(tex_path, *, output_dir):
        pdf = Path(output_dir) / "INTERIOR.pdf"
        pdf.write_bytes(b"%PDF-test")
        return LatexCompileResult(pdf, ("lualatex",), "", "")

    monkeypatch.setattr(cli, "compile_lualatex", fake_compile)
    monkeypatch.setattr(cli, "inspect_pdf_page_count", lambda _path: 6)

    status = cli.main(
        ["build-interior", "--input", str(source), "--output-dir", str(output)]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["status"] == "built"
    assert payload["page_count"] == 6
    assert (output / "INTERIOR.tex").is_file()
    assert (output / "INTERIOR.pdf").is_file()


def test_cli_rejects_empty_chapter_body(tmp_path, capsys):
    payload = _book_payload()
    payload["chapters"] = [{"title": "Chapter 1", "body": ""}]
    source = tmp_path / "book.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    status = cli.main(
        ["build-interior", "--input", str(source), "--output-dir", str(tmp_path / "out")]
    )

    assert status == 2
    assert "must be a non-empty string" in capsys.readouterr().err


def test_print_engine_has_no_writer_or_django_imports():
    package_root = Path(cli.__file__).resolve().parent

    offenders = []
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        if any(
            module == "django"
            or module.startswith("django.")
            or module == "writer"
            or module.startswith("writer.")
            or module == "web.writer"
            or module.startswith("web.writer.")
            or module == "gaiden.writer_engine"
            or module.startswith("gaiden.writer_engine.")
            for module in imported_modules
        ):
            offenders.append(str(path.relative_to(package_root)))

    assert offenders == []
