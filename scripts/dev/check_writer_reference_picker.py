"""Browser smoke checks; use a disposable database for --create-project."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="/tmp/gaiden-reference-picker")
    parser.add_argument("--create-project", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        errors = []
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        catalog_requests = []
        page.on("request", lambda request: catalog_requests.append(request.url) if "/works/" in request.url else None)
        page.goto(args.url + "/writer/projects/new/")
        page.wait_for_load_state("networkidle")
        assert not catalog_requests, catalog_requests
        page.locator("#id_title").fill("Browser reference selection check")
        page.get_by_role("button", name="Selecionar idiomas", exact=True).click()
        page.locator('input[name="project_languages"][value="pt-BR"]').check()
        page.get_by_role("button", name="Concluir", exact=True).click()
        page.locator("#id_rules_en").fill("Independent English rule")
        page.locator('[id="id_rules_pt-BR"]').fill("Independent Portuguese rule")
        page.locator("#id_chapter_count").fill("34")
        page.get_by_role("button", name="Selecionar referências", exact=True).click()
        page.locator("#author-search").fill("Agatha Christie")
        expect(page.locator(".author-button")).to_have_count(1)
        page.locator(".author-button").filter(has_text="Agatha Christie").click()
        page.get_by_role("checkbox", name="The Mysterious Affair at Styles", exact=True).check()
        page.get_by_role("checkbox", name="The Murder of Roger Ackroyd", exact=True).check()
        assert not page.locator("#work-list").get_by_text("A Study in Scarlet", exact=True).count()
        page.locator("#author-search").fill("Arthur Conan Doyle")
        expect(page.locator(".author-button")).to_have_count(1)
        page.locator(".author-button").filter(has_text="Arthur Conan Doyle").click()
        page.get_by_role("checkbox", name="A Study in Scarlet", exact=True).check()
        assert not page.locator("#work-list").get_by_text("The Mysterious Affair at Styles", exact=True).count()
        page.screenshot(path=str(output / "desktop-selector.png"))
        page.get_by_role("button", name="Adicionar selecionadas ao projeto", exact=True).click()
        assert "Agatha Christie · 2 obras" in page.locator("#reference-summary").inner_text()
        rows = json.loads(page.locator("#id_work_references").input_value())
        assert len(rows) == 3 and all(row["work_id"] for row in rows)
        page.locator("#open-references").click()
        page.locator("#selected-details summary").click()
        page.locator("#selected-reference-list select").first.select_option("MYSTERY_STRUCTURE")
        page.locator("#selected-reference-list textarea").first.fill("Selected mystery structure reference")
        page.locator("#cancel-references").click()
        assert page.locator("#id_work_references").input_value() == json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
        page.screenshot(path=str(output / "desktop-form.png"), full_page=True)
        if args.create_project:
            page.get_by_role("button", name="Salvar e criar tabela de capítulos", exact=True).click()
            page.wait_for_url("**/writer/projects/*/")
            page.locator('a[href$="/edit/"]').first.click()
            page.wait_for_url("**/edit/")
            assert page.locator("#id_rules_en").input_value() == "Independent English rule"
            assert page.locator('[id="id_rules_pt-BR"]').input_value() == "Independent Portuguese rule"
            assert len(json.loads(page.locator("#id_work_references").input_value())) == 3
            assert page.get_by_text("34 capítulos existentes", exact=True).count()
            page.get_by_role("button", name="Salvar alterações", exact=True).click()
            page.wait_for_url("**/writer/projects/*/")
            page.locator('a[href$="/edit/"]').first.click()
            assert page.get_by_text("34 capítulos existentes", exact=True).count()
        for width in (768, 390):
            page.set_viewport_size({"width": width, "height": 844})
            page.goto(args.url + "/writer/projects/new/")
            page.wait_for_load_state("networkidle")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#open-references").click()
            page.locator("#author-search").fill("Agatha Christie")
            expect(page.locator(".author-button")).to_have_count(1)
            page.locator(".author-button").filter(has_text="Agatha Christie").click()
            page.get_by_role("checkbox", name="The Mysterious Affair at Styles", exact=True).check()
            assert page.locator("#reference-selector").evaluate("(el) => el.scrollWidth <= el.clientWidth")
            page.screenshot(path=str(output / ("selector-" + str(width) + ".png")))
            page.locator("#apply-references").click()
        browser.close()
        assert not errors, errors
        print("Browser checks passed: author isolation, selection, cancel, languages, desktop/mobile; JS errors: 0")


if __name__ == "__main__":
    main()
