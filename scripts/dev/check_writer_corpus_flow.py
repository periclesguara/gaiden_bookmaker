"""Seed only the disposable writer_flow DB; exercise the actual operator UI."""
import argparse
import json
import os
from pathlib import Path
import sys


def seed():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "web"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings_ci")
    import django
    django.setup()
    from django.conf import settings
    if settings.DATABASES["default"]["NAME"] != "writer_flow":
        raise RuntimeError("Browser fixtures require the disposable writer_flow database")
    from editorial.models import Contributor, Edition, Language, Seal, Work
    from web.writer.models import Chapter, StoryProject
    from web.writer.services.corpus import save_corpus_selection

    language, _ = Language.objects.get_or_create(code="browser-en", defaults={"name": "English"})
    seal, _ = Seal.objects.get_or_create(slug="browser-test", defaults={"name": "Test"})
    root = Path(os.environ["GAIDEN_STORAGE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    authors = {}
    works = []
    for name, count in (("Arthur Conan Doyle", 4), ("Agatha Christie", 24)):
        author, _ = Contributor.objects.get_or_create(name=name)
        authors[name] = author
        for number in range(count):
            title = ("The Murder of Roger Ackroyd" if name == "Agatha Christie" and number == 0
                     else name + " Reference " + str(number + 1))
            work, _ = Work.objects.get_or_create(code="browser-" + str(author.pk) + "-" + str(number),
                defaults={"title": title, "author": author, "original_language": language})
            raw = root / (work.code + ".txt")
            raw.write_text("# CHAPTER 1\n\n" + "Holmes discovers a clue in the telegram. " * 100, encoding="utf-8")
            Edition.objects.get_or_create(work=work, language=language, seal=seal, defaults={"raw_source_path": str(raw)})
            works.append(work)
    project, _ = StoryProject.objects.get_or_create(title="Writer corpus browser check", defaults={"language": "en", "chapter_count": 3})
    for number in range(1, 4):
        Chapter.objects.get_or_create(project=project, number=number, defaults={"title": "The Telegram " + str(number),
                                     "direction": "Investigate the telegram", "script": "Holmes finds a clue."})
    save_corpus_selection(project, [work for work in works if work.author.name == "Arthur Conan Doyle"])
    print(project.pk)


def check(args):
    from playwright.sync_api import expect, sync_playwright
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    url = args.url.rstrip("/") + "/writer/projects/" + str(args.project_id) + "/"
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#project-corpus")).to_contain_text("Arthur Conan Doyle")
        assert not page.locator("#work-list .reference-work").count()
        page.get_by_role("button", name="Selecionar obras", exact=True).click()
        page.locator("#corpus-search").fill("Roger Ackroyd")
        expect(page.get_by_role("checkbox", name="The Murder of Roger Ackroyd", exact=True)).to_be_visible()
        page.get_by_role("checkbox", name="The Murder of Roger Ackroyd", exact=True).check()
        page.get_by_role("button", name="Salvar seleção", exact=True).click()
        page.wait_for_url(url)
        expect(page.locator("#project-corpus")).to_contain_text("Total: 5 obras")
        page.get_by_role("button", name="Selecionar obras", exact=True).click()
        page.locator("#corpus-search").fill("Agatha")
        page.locator(".author-result").filter(has_text="Agatha Christie").click()
        expect(page.locator(".reference-work")).to_have_count(20)
        page.get_by_role("button", name="Selecionar todas deste autor", exact=True).click()
        expect(page.locator("#draft-count")).to_have_text("28 obras selecionadas")
        page.screenshot(path=str(output / "desktop-selector.png"))
        page.get_by_role("button", name="Salvar seleção", exact=True).click()
        page.wait_for_url(url)
        expect(page.locator("#project-corpus")).to_contain_text("Total: 28 obras")
        page.reload()
        expect(page.locator("#project-corpus")).to_contain_text("Total: 28 obras")
        page.get_by_role("button", name="Preparar pendentes", exact=True).click()
        page.wait_for_url(url)
        expect(page.locator(".corpus-status")).to_contain_text("28/28")
        page.get_by_role("button", name="Selecionar obras", exact=True).click()
        page.locator("#corpus-search").fill("Agatha")
        page.locator(".author-result").filter(has_text="Agatha Christie").click()
        page.get_by_role("button", name="Limpar autor", exact=True).click()
        expect(page.locator("#draft-count")).to_have_text("4 obras selecionadas")
        page.get_by_role("button", name="Cancelar", exact=True).click()
        expect(page.locator("#project-corpus")).to_contain_text("Total: 28 obras")
        page.screenshot(path=str(output / "desktop-project.png"), full_page=True)
        for width in (768, 390):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(output / f"project-{width}.png"), full_page=True)
            page.get_by_role("button", name="Selecionar obras", exact=True).click()
            page.locator("#corpus-search").fill("Roger Ackroyd")
            expect(page.get_by_role("checkbox", name="The Murder of Roger Ackroyd", exact=True)).to_be_checked()
            assert page.locator("#reference-selector").evaluate("el => el.scrollWidth <= el.clientWidth")
            page.screenshot(path=str(output / f"selector-{width}.png"))
            page.get_by_role("button", name="Cancelar", exact=True).click()
        page.goto(args.url.rstrip("/") + "/writer/projects/new/")
        page.get_by_role("button", name="Selecionar obras", exact=True).click()
        page.locator("#corpus-search").fill("Roger Ackroyd")
        page.get_by_role("checkbox", name="The Murder of Roger Ackroyd", exact=True).check()
        page.get_by_role("button", name="Salvar seleção", exact=True).click()
        assert len(json.loads(page.locator("#id_work_references").input_value())) == 1
        assert not errors, errors
        browser.close()
    print("Browser checks passed: title/author search, full-author batch, additive save/reload, preparation, cancel, desktop/mobile, registration picker; JS errors: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--output", default="/tmp/gaiden-writer-corpus-browser")
    args = parser.parse_args()
    if args.seed:
        seed()
    elif args.project_id:
        check(args)
    else:
        parser.error("--project-id or --seed is required")
