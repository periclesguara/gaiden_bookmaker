"""Versioned physical production. All entrypoints verify the editorial source."""
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
import math
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from django.utils.text import slugify
from PIL import Image

from django.db import transaction
from django.conf import settings

from gaiden.infrastructure import storage
from gaiden.print_engine.compiler import compile_lualatex
from gaiden.print_engine.image_enhancement import enhance_print_image
from gaiden.print_engine.isbn import isbn13
from gaiden.print_engine.manuscript import manuscript_tex
from gaiden.print_engine.preflight import inspect_pdf_page_count, preflight_pdfs
from gaiden.print_engine.providers.ingramspark import IngramSparkAdapter
from gaiden.print_engine.providers.industrial import validate_industrial
from gaiden.print_engine.specs import PrintSpec
from .models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport


_OPENING_IMAGE_RE = re.compile(r"(?:src|href)=[\"']\.\./images/([^\"']+)[\"']", re.IGNORECASE)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_SIMULATOR_CALIPER_IN = {"white": Decimal("0.00225"), "cream": Decimal("0.00250")}


def sha(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256") if hasattr(hashlib, "file_digest") else None
        if digest is None:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def snapshot(edition):
    typography = edition.typography
    return {"source": edition.source_sha256, "platform": edition.platform,
            "profile": edition.profile_version, "trim": edition.trim, "paper": edition.paper,
            "binding": edition.binding, "color": edition.interior_color, "isbn": edition.isbn,
            "barcode_mode": edition.barcode_mode,
            "font": typography.font, "size": str(typography.size_pt),
            "leading": str(typography.leading_pt), "license": typography.license_reference}


def invalidate(edition):
    for model in (InteriorBuild, CoverBuild, PreflightReport, PrintExport):
        model.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
    edition.state = PrintEdition.State.INVALIDATED
    edition.save(update_fields=["state", "updated_at"])


def guard(edition):
    candidate = Path(edition.source_path).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else storage.resolve_repo_path(candidate).resolve()
    roots = [storage.storage_root().resolve(), storage.repo_root().resolve()]
    valid = any(path.is_relative_to(root) for root in roots)
    if not valid or not path.is_file() or sha(path) != edition.source_sha256 or edition.source.status != "DONE" or not edition.source.qualifies_as_done:
        invalidate(edition)
        raise ValueError("Fonte editorial alterada ou aprovação inválida. Crie uma nova edição a partir da fonte final aprovada.")
    for model in (InteriorBuild, CoverBuild, PreflightReport, PrintExport):
        for build in model.objects.filter(edition=edition, status="READY"):
            malformed = any(
                not isinstance(item, dict)
                or not item.get("path")
                or not item.get("sha256")
                or not Path(item["path"]).is_file()
                or sha(item["path"]) != item["sha256"]
                for item in build.files.values()
            )
            if build.snapshot != snapshot(edition) or malformed:
                invalidate(edition)
                raise ValueError("Configuração ou artefato alterado; builds invalidados. Compile novamente.")
    return path


def spec_for(edition):
    if edition.platform != "ingramspark" or edition.binding != "paperback" or edition.interior_color != "black_and_white":
        raise ValueError("Perfil não suportado pelo MVP.")
    width, height = edition.trim.split("x")
    return PrintSpec(trim_width_in=width, trim_height_in=height, paper=edition.paper,
                     body_font=edition.typography.font, body_font_size_pt=edition.typography.size_pt,
                     body_leading_pt=edition.typography.leading_pt)


def estimated_spine_width(edition, page_count):
    """Return a simulator-only spine estimate; provider approval still needs its template."""
    coefficient = _SIMULATOR_CALIPER_IN.get(edition.paper, Decimal("0.00225"))
    return (Decimal(page_count) * coefficient).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def estimated_cover_geometry(edition, page_count):
    spine = estimated_spine_width(edition, page_count)
    spec = spec_for(edition).finalize(page_count=page_count, spine_width_in=spine)
    return geometry_payload(IngramSparkAdapter.cover_geometry(spec))


def root_for(build):
    return storage.storage_root() / "printbook" / str(build.edition_id) / build._meta.model_name / f"v{build.version:04d}"


@transaction.atomic
def new_build(model, edition, **kwargs):
    edition = PrintEdition.objects.select_for_update().get(pk=edition.pk)
    if any(m.objects.filter(edition=edition, status="RUNNING").exists() for m in (InteriorBuild, CoverBuild, PreflightReport, PrintExport)):
        raise ValueError("Já existe uma operação em andamento nesta edição.")
    version = (model.objects.filter(edition=edition).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    build = model.objects.create(edition=edition, version=version, snapshot=snapshot(edition), **kwargs)
    root_for(build).mkdir(parents=True, exist_ok=False)
    return build


def geometry_payload(geometry):
    payload = json.loads(json.dumps(asdict(geometry), default=str))
    payload["fingerprint"] = geometry.fingerprint
    return payload


def record(build, paths, state):
    build.files = {key: {"path": str(path), "sha256": sha(path)} for key, path in paths.items()}
    build.status = "READY"
    build.save()
    build.edition.state = state
    build.edition.save(update_fields=["state", "updated_at"])


def fail(build, exc):
    build.status = "FAILED"
    message = str(exc) if isinstance(exc, ValueError) else "Ferramenta de produção ou sistema de arquivos indisponível."
    build.findings = [{
        "code": "build.failed",
        "severity": "BLOCKER",
        "message": f"{message} O build anterior permanece no histórico.",
    }]
    build.save()


def compile_interior(edition):
    path = guard(edition)
    spec = spec_for(edition)
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Manuscrito excede 50 MiB.")
    if not shutil.which("lualatex") or not shutil.which("pdfinfo"):
        raise ValueError("Instale LuaLaTeX e Poppler para compilar o miolo.")
    build = new_build(InteriorBuild, edition)
    try:
        root = root_for(build)
        image_manifest, image_files = extract_print_illustrations(edition.source, root)
        tex = manuscript_tex(
            path.read_text(encoding="utf-8-sig"),
            spec,
            image_manifest=image_manifest,
            language_code=edition.source.language_code,
        )
        source = root / "INTERIOR.tex"
        source.write_text(tex, encoding="utf-8")
        result = compile_lualatex(source, output_dir=root)
        guard(edition)
        build.page_count = inspect_pdf_page_count(result.pdf_path)
        build.tools = {
            "lualatex": subprocess.run(["lualatex", "--version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0],
            "illustrations": len(image_manifest),
        }
        # A new interior makes every previous cover and approval stale.
        for model in (CoverBuild, PreflightReport, PrintExport):
            statuses = ["READY", "DRAFT", "BLOCKED"] if model is CoverBuild else ["READY"]
            model.objects.filter(edition=edition, status__in=statuses).update(status="INVALIDATED")
        files = {"pdf": result.pdf_path, "tex": source}
        files.update(image_files)
        record(build, files, PrintEdition.State.INTERIOR_READY)
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def extract_print_illustrations(source, root):
    """Copy only approved chapter-opening figures from the final EPUB.

    The print build uses the same illustration mapping already approved by the
    Intake EPUB. It never scans unrelated storage or invents chapter matches.
    """
    epub_path = source._safe_existing_path(source.epub_path, require_epub=True)
    manifest = {}
    files = {}
    target_dir = root / "illustrations"
    target_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    with zipfile.ZipFile(epub_path) as archive:
        for member in archive.namelist():
            match = re.fullmatch(r"EPUB/text/chapter_(\d+)_opening\.xhtml", member)
            if not match:
                continue
            chapter_number = int(match.group(1))
            markup = archive.read(member).decode("utf-8")
            image_match = _OPENING_IMAGE_RE.search(markup)
            if not image_match:
                continue
            image_name = Path(image_match.group(1)).name
            if image_name != image_match.group(1) or Path(image_name).suffix.lower() not in _IMAGE_EXTENSIONS:
                raise ValueError("A ilustração aprovada contém um caminho inválido.")
            image_member = f"EPUB/images/{image_name}"
            try:
                info = archive.getinfo(image_member)
            except KeyError as exc:
                raise ValueError(f"A ilustração do capítulo {chapter_number} não está no EPUB aprovado.") from exc
            if info.file_size > 50 * 1024 * 1024 or total_bytes + info.file_size > 500 * 1024 * 1024:
                raise ValueError("As ilustrações aprovadas excedem o limite do build de impressão.")
            target = target_dir / f"FIGURE_{chapter_number:03d}{Path(image_name).suffix.lower()}"
            original = target.with_name(f".{target.name}.source")
            original.write_bytes(archive.read(image_member))
            enhance_print_image(original, target, "print_safe")
            original.unlink()
            total_bytes += info.file_size
            relative = f"illustrations/{target.name}"
            manifest[chapter_number] = relative
            files[f"illustration_{chapter_number:03d}"] = target
    return manifest, files


def latest(model, edition):
    result = model.objects.filter(edition=edition, status="READY").first()
    if result is None:
        raise ValueError("Conclua o build anterior antes desta etapa.")
    return result


def load_cover_design(build):
    """Read a versioned design only when its stored hash still matches."""
    if not build or not build.files.get("design"):
        return {}
    item = build.files["design"]
    path = Path(item["path"])
    if not path.is_file() or sha(path) != item["sha256"]:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def upload_pdf(upload, path):
    if Path(upload.name).suffix.lower() != ".pdf" or upload.size > 100 * 1024 * 1024:
        raise ValueError("Envie um PDF de até 100 MiB.")
    with path.open("xb") as output:
        total = 0
        for chunk in upload.chunks():
            total += len(chunk)
            if total > 100 * 1024 * 1024:
                raise ValueError("PDF excede 100 MiB.")
            output.write(chunk)
    with path.open("rb") as stream:
        if not stream.read(8).startswith(b"%PDF-"):
            raise ValueError("O conteúdo enviado não é PDF.")
    inspect_pdf_page_count(path)
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError("O PDF enviado não pôde ser inspecionado.")
    metadata = result.stdout
    if re.search(r"^Encrypted:\s+yes\b", metadata, re.IGNORECASE | re.MULTILINE):
        raise ValueError("PDF criptografado não é aceito.")
    if re.search(r"^JavaScript:\s+yes\b", metadata, re.IGNORECASE | re.MULTILINE):
        raise ValueError("PDF com JavaScript não é aceito.")


def upload_cover_image(upload, path):
    """Store one bounded raster image under a fixed runtime filename."""
    suffix = Path(upload.name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg"} or upload.size > 50 * 1024 * 1024:
        raise ValueError("A imagem da frente deve ser PNG/JPEG de até 50 MiB.")
    try:
        from PIL import Image, UnidentifiedImageError

        upload.seek(0)
        with Image.open(upload) as image:
            dimensions = image.size
            image.verify()
        upload.seek(0)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("A imagem da frente não pôde ser lida.") from exc
    with path.open("xb") as output:
        for chunk in upload.chunks():
            output.write(chunk)
    return dimensions


def import_cover(edition, cover, template, spine):
    guard(edition)
    interior = latest(InteriorBuild, edition)
    spec = spec_for(edition).finalize(page_count=interior.page_count, spine_width_in=spine)
    geometry = IngramSparkAdapter.cover_geometry(spec)
    build = new_build(CoverBuild, edition, interior=interior, geometry=geometry_payload(geometry))
    try:
        root = root_for(build)
        upload_pdf(cover, root / "COVER.pdf")
        upload_pdf(template, root / "OFFICIAL_TEMPLATE.pdf")
        if inspect_pdf_page_count(root / "COVER.pdf") != 1 or inspect_pdf_page_count(root / "OFFICIAL_TEMPLATE.pdf") != 1:
            raise ValueError("Capa paperback e template devem ter uma página.")
        validate_pdf_page_size(
            root / "COVER.pdf",
            width_in=geometry.spread_width_in,
            height_in=geometry.spread_height_in,
            label="A capa completa",
        )
        validate_pdf_page_size(
            root / "OFFICIAL_TEMPLATE.pdf",
            width_in=geometry.spread_width_in,
            height_in=geometry.spread_height_in,
            label="O template oficial",
        )
        guard(edition)
        for model in (PreflightReport, PrintExport):
            model.objects.filter(edition=edition, status="READY").update(status="INVALIDATED")
        record(build, {"pdf": root / "COVER.pdf", "template": root / "OFFICIAL_TEMPLATE.pdf"}, PrintEdition.State.COVER_READY)
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def design_cover(
    edition,
    *,
    layout,
    add_front_text=False,
    title,
    subtitle,
    author,
    back_text,
    back_heading="",
    back_quote="",
    back_bio="",
    spine_text,
    background_color,
    text_color,
    accent_color,
    back_palette="midnight",
    back_background_color="#1F2937",
    back_text_color="#FFFFFF",
    back_accent_color="#D4A24C",
    template=None,
    spine=None,
    cover_image=None,
    image_fit="cover",
    image_zoom=100,
    image_x=50,
    image_y=50,
    overlay_opacity=25,
    enhancement_profile="print_safe",
    save_mode="draft",
):
    """Generate a local text-first cover from operator-selected design data."""
    from gaiden.print_engine.cover_composition import design_cover_tex

    guard(edition)
    interior = latest(InteriorBuild, edition)
    previous = CoverBuild.objects.filter(
        edition=edition, interior=interior, status__in=["DRAFT", "BLOCKED", "READY"]
    ).first()
    previous_design = load_cover_design(previous)
    spine_text = (spine_text or "").strip() or previous_design.get("spine_text") or edition.name
    if save_mode not in {"draft", "final"}:
        raise ValueError("Modo de salvamento da capa inválido.")
    if save_mode == "final" and template is None:
        raise ValueError("Para salvar a capa final, anexe o template oficial da IngramSpark.")
    spine = spine or estimated_spine_width(edition, interior.page_count)
    spec = spec_for(edition).finalize(page_count=interior.page_count, spine_width_in=spine)
    geometry = IngramSparkAdapter.cover_geometry(spec)
    executable = shutil.which("lualatex")
    if not executable:
        raise ValueError("Instale LuaLaTeX para montar a capa.")
    build = new_build(CoverBuild, edition, interior=interior, geometry=geometry_payload(geometry))
    try:
        root = root_for(build)
        template_path = None
        if template is not None:
            template_path = root / "OFFICIAL_TEMPLATE.pdf"
            upload_pdf(template, template_path)
            if inspect_pdf_page_count(template_path) != 1:
                raise ValueError("O template oficial deve ter uma página.")
            validate_pdf_page_size(
                template_path,
                width_in=geometry.spread_width_in,
                height_in=geometry.spread_height_in,
                label="O template oficial",
            )
        image_name = None
        image_path = None
        original_image_path = None
        image_dimensions = (None, None)
        enhancement = None
        if cover_image is not None:
            suffix = Path(cover_image.name).suffix.lower()
            image_name = "COVER_IMAGE" + suffix
            image_path = root / image_name
            original_image_path = root / ("COVER_IMAGE_ORIGINAL" + suffix)
            image_dimensions = upload_cover_image(cover_image, original_image_path)
            zoom = Decimal(str(image_zoom)) / Decimal("100")
            print_width = geometry.trim_width_in + geometry.bleed_in
            print_height = geometry.spread_height_in
            minimum_width = math.ceil(float(print_width * zoom * Decimal("300")))
            minimum_height = math.ceil(float(print_height * zoom * Decimal("300")))
            enhancement = enhance_print_image(
                original_image_path,
                image_path,
                enhancement_profile,
                minimum_width_px=minimum_width,
                minimum_height_px=minimum_height,
            )
            image_dimensions = (enhancement["width_px"], enhancement["height_px"])
        elif previous and previous.files.get("cover_image_original"):
            previous_original = Path(previous.files["cover_image_original"]["path"])
            if previous_original.is_file() and sha(previous_original) == previous.files["cover_image_original"]["sha256"]:
                suffix = previous_original.suffix.lower()
                image_name = "COVER_IMAGE" + suffix
                original_image_path = root / ("COVER_IMAGE_ORIGINAL" + suffix)
                image_path = root / image_name
                shutil.copy2(previous_original, original_image_path)
                with Image.open(original_image_path) as source_image:
                    image_dimensions = source_image.size
                zoom = Decimal(str(image_zoom)) / Decimal("100")
                enhancement = enhance_print_image(
                    original_image_path,
                    image_path,
                    enhancement_profile,
                    minimum_width_px=math.ceil(float((geometry.trim_width_in + geometry.bleed_in) * zoom * Decimal("300"))),
                    minimum_height_px=math.ceil(float(geometry.spread_height_in * zoom * Decimal("300"))),
                )
                image_dimensions = (enhancement["width_px"], enhancement["height_px"])
        design = {
            "layout": layout,
            "add_front_text": add_front_text,
            "title": title,
            "subtitle": subtitle,
            "author": author,
            "back_text": back_text,
            "back_heading": back_heading,
            "back_quote": back_quote,
            "back_bio": back_bio,
            "spine_text": spine_text if geometry.spine_text_allowed else "",
            "background_color": background_color,
            "text_color": text_color,
            "accent_color": accent_color,
            "back_palette": back_palette,
            "back_background_color": back_background_color,
            "back_text_color": back_text_color,
            "back_accent_color": back_accent_color,
            "page_count": interior.page_count,
            "spine_width_in": str(spine),
            "geometry_source": "official_template" if template_path else "simulator_estimate",
            "image_fit": image_fit,
            "image_zoom": image_zoom,
            "image_x": image_x,
            "image_y": image_y,
            "overlay_opacity": overlay_opacity,
            "enhancement_profile": enhancement_profile,
            "image_enhancement": enhancement,
            "barcode_mode": edition.barcode_mode,
            "save_mode": save_mode,
        }
        design_findings = []
        if enhancement and enhancement["resized"]:
            design_findings.append({
                "code": "cover.image_upscaled",
                "severity": "WARNING",
                "message": (
                    f"OpenCV ampliou a arte em {enhancement['scale_factor']:.2f}× com Lanczos para atingir "
                    "300 ppi efetivos. Confira rostos, letras e detalhes finos na prova antes do envio."
                ),
            })
        if image_dimensions[0] and image_dimensions[1]:
            zoom = Decimal(str(image_zoom)) / Decimal("100")
            effective_ppi = min(
                Decimal(image_dimensions[0]) / ((geometry.trim_width_in + geometry.bleed_in) * zoom),
                Decimal(image_dimensions[1]) / (geometry.spread_height_in * zoom),
            )
            design["effective_image_ppi"] = int(effective_ppi)
            if effective_ppi < 300:
                design_findings.append({
                    "code": "cover.image_resolution",
                    "severity": "BLOCKER",
                    "message": (
                        f"A imagem fornece aproximadamente {int(effective_ppi)} ppi no tamanho final; "
                        "use uma imagem que mantenha pelo menos 300 ppi após o enquadramento."
                    ),
                })
        design_path = root / "DESIGN.json"
        design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
        tex = root / "COVER.tex"
        tex.write_text(
            design_cover_tex(
                geometry,
                title=title,
                subtitle=subtitle,
                author=author,
                back_text=back_text,
                back_heading=back_heading,
                back_quote=back_quote,
                back_bio=back_bio,
                spine_text=spine_text,
                background_color=background_color,
                text_color=text_color,
                accent_color=accent_color,
                back_background_color=back_background_color,
                back_text_color=back_text_color,
                back_accent_color=back_accent_color,
                layout=layout,
                image_name=image_name,
                image_width_px=image_dimensions[0],
                image_height_px=image_dimensions[1],
                image_fit=image_fit,
                image_zoom=image_zoom,
                image_x=image_x,
                image_y=image_y,
                overlay_opacity=overlay_opacity,
                reserve_barcode=edition.barcode_mode == "provider",
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "COVER.tex"],
            cwd=root, capture_output=True, timeout=180, check=False,
        )
        compiled_pdf = root / "COVER.pdf"
        pdf = root / f"{slugify(edition.name) or 'book'}-cover.pdf"
        if result.returncode or not compiled_pdf.is_file() or inspect_pdf_page_count(compiled_pdf) != 1:
            raise ValueError("Não foi possível gerar a capa do modelo escolhido.")
        compiled_pdf.replace(pdf)
        guard(edition)
        build.tools = {
            "lualatex": tool_version("lualatex"),
            "designer": "gaiden-cover-studio-v3",
            "image_engine": enhancement["engine"] if enhancement else None,
        }
        build.findings = design_findings
        files = {"pdf": pdf, "tex": tex, "design": design_path}
        if template_path:
            files["template"] = template_path
        if image_path:
            files["cover_image"] = image_path
            files["cover_image_original"] = original_image_path
        has_blocker = any(item["severity"] == "BLOCKER" for item in design_findings)
        if save_mode == "final" and template_path and not has_blocker:
            for model in (PreflightReport, PrintExport):
                model.objects.filter(edition=edition, status="READY").update(status="INVALIDATED")
            record(build, files, PrintEdition.State.COVER_READY)
        else:
            build.files = {key: {"path": str(path), "sha256": sha(path)} for key, path in files.items()}
            build.status = "BLOCKED" if save_mode == "final" else "DRAFT"
            build.findings = design_findings
            if not template_path:
                build.findings.append({
                    "code": "cover.template_pending",
                    "severity": "WARNING",
                    "message": "Prova visual gerada com lombada estimada. Anexe o template oficial antes do preflight.",
                })
            build.save()
            edition.state = PrintEdition.State.COVER_PENDING
            edition.save(update_fields=["state", "updated_at"])
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def preflight(edition):
    guard(edition)
    interior, cover = latest(InteriorBuild, edition), latest(CoverBuild, edition)
    if cover.interior_id != interior.id:
        raise ValueError("A capa não pertence ao miolo atual.")
    build = new_build(PreflightReport, edition, interior=interior, cover=cover)
    try:
        spec = spec_for(edition).finalize(page_count=interior.page_count, spine_width_in=cover.geometry["spine_width_in"])
        report = preflight_pdfs(spec, interior_pdf=interior.files["pdf"]["path"], cover_pdf=cover.files["pdf"]["path"])
        build.findings = [{"code": c.code, "severity": "INFO" if c.status.value == "pass" else "BLOCKER", "message": c.message} for c in report.checks if c.code != "pdfx.validation"]
        build.findings.extend(cover.findings)
        hashes = {"interior": interior.files["pdf"]["sha256"], "cover": cover.files["pdf"]["sha256"], "template": cover.files["template"]["sha256"]}
        job = root_for(build) / "validation-job.json"
        job.write_text(json.dumps({"hashes": hashes, "configuration": snapshot(edition), "geometry": cover.geometry,
            "interior": interior.files["pdf"]["path"], "cover": cover.files["pdf"]["path"],
            "template": cover.files["template"]["path"], "page_count": interior.page_count}, indent=2), encoding="utf-8")
        evidence = validate_industrial(getattr(settings, "PRINTBOOK_VALIDATOR", ""), job, hashes)
        build.findings.extend(evidence["checks"])
        build.tools = {
            "pdfinfo": tool_version("pdfinfo"),
            "pdffonts": tool_version("pdffonts"),
            "industrial_validator": evidence["tool_version"],
            "pdfx_standard": evidence["pdfx_standard"],
        }
        if not edition.isbn:
            build.findings.append({"code": "isbn", "severity": "BLOCKER", "message": "Informe o ISBN desta edição física."})
        elif isbn13(edition.isbn):
            build.findings.append({"code": "isbn", "severity": "INFO", "message": "Dígito ISBN-13 válido; titularidade depende do cadastro editorial."})
        output = root_for(build) / "preflight.json"
        output.write_text(json.dumps({
            "edition_id": edition.id,
            "configuration": snapshot(edition),
            "hashes": hashes,
            "tools": build.tools,
            "checks": build.findings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        guard(edition)
        passed = not any(f["severity"] in {"BLOCKER", "ERROR"} for f in build.findings)
        record(build, {"report": output}, PrintEdition.State.PRINT_READY if passed else PrintEdition.State.PREFLIGHT_PENDING)
        if not passed:
            build.status = "BLOCKED"
            build.save(update_fields=["status"])
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def export_package(edition):
    guard(edition)
    report = latest(PreflightReport, edition)
    if edition.state not in {PrintEdition.State.PRINT_READY, PrintEdition.State.EXPORTED} or any(f["severity"] in {"ERROR", "BLOCKER"} for f in report.findings):
        raise ValueError("Exportação bloqueada pelo preflight.")
    if report.interior_id != latest(InteriorBuild, edition).id or report.cover_id != latest(CoverBuild, edition).id:
        raise ValueError("Preflight desatualizado.")
    build = new_build(PrintExport, edition, report=report)
    try:
        root = root_for(build)
        interior_preview = render_preview(report.interior.files["pdf"]["path"], root / "INTERIOR_PREVIEW.png")
        cover_preview = render_preview(report.cover.files["pdf"]["path"], root / "COVER_PREVIEW.png")
        output = root / "PRINT_PACKAGE.zip"
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as package:
            for prefix, item in (("interior", report.interior), ("cover", report.cover), ("preflight", report)):
                for key, artifact in item.files.items():
                    package.write(artifact["path"], f"{prefix}/{Path(artifact['path']).name}")
            package.write(interior_preview, f"preview/{interior_preview.name}")
            package.write(cover_preview, f"preview/{cover_preview.name}")
            artifacts = {
                "interior": {key: {"filename": Path(item["path"]).name, "sha256": item["sha256"]} for key, item in report.interior.files.items()},
                "cover": {key: {"filename": Path(item["path"]).name, "sha256": item["sha256"]} for key, item in report.cover.files.items()},
                "preflight": {key: {"filename": Path(item["path"]).name, "sha256": item["sha256"]} for key, item in report.files.items()},
                "preview": {
                    "interior": {"filename": interior_preview.name, "sha256": sha(interior_preview)},
                    "cover": {"filename": cover_preview.name, "sha256": sha(cover_preview)},
                },
            }
            package.writestr("metadata.json", json.dumps({
                "edition_id": edition.id,
                "platform": edition.platform,
                "configuration": snapshot(edition),
                "artifacts": artifacts,
                "build_versions": {"interior": report.interior.version, "cover": report.cover.version, "preflight": report.version},
            }, indent=2))
        guard(edition)
        record(build, {"package": output, "interior_preview": interior_preview, "cover_preview": cover_preview}, PrintEdition.State.EXPORTED)
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def render_preview(pdf_path, output_path):
    executable = shutil.which("pdftoppm")
    if not executable:
        raise ValueError("Poppler pdftoppm é obrigatório para gerar previews do pacote.")
    target = Path(output_path)
    prefix = target.with_suffix("")
    result = subprocess.run(
        [executable, "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1500", "-png", str(pdf_path), str(prefix)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode or not target.is_file():
        raise ValueError("Não foi possível gerar o preview do pacote.")
    return target


def tool_version(command):
    executable = shutil.which(command)
    if not executable:
        return "unavailable"
    try:
        result = subprocess.run([executable, "-v" if command == "pdffonts" else "-v"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    output = (result.stdout or result.stderr).splitlines()
    return output[0][:300] if output else "unknown"


def inspect_pdf_page_size(path):
    result = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "1", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError("O tamanho do PDF não pôde ser verificado.")
    for line in result.stdout.splitlines():
        match = re.match(r"Page\s+1 size:\s+([0-9.]+) x ([0-9.]+) pts", line)
        if match:
            return float(match.group(1)), float(match.group(2))
    raise ValueError("O PDF não informa dimensões de página verificáveis.")


def validate_pdf_page_size(path, *, width_in, height_in, label):
    """Require a PDF page box to match the geometry used by this edition."""
    width, height = inspect_pdf_page_size(path)
    tolerance_points = 0.72  # 0.01 in; allows provider rounding in PDF boxes.
    expected_width = float(width_in) * 72
    expected_height = float(height_in) * 72
    if abs(width - expected_width) > tolerance_points or abs(height - expected_height) > tolerance_points:
        raise ValueError(
            f"{label} deve medir {width_in} × {height_in} polegadas; "
            f"o PDF enviado mede {width / 72:.3f} × {height / 72:.3f} polegadas."
        )


def compose_cover(edition, front, back, template, spine):
    """Version a full wrap from operator-supplied panels; do not approve it."""
    from gaiden.print_engine.cover_composition import cover_tex

    guard(edition)
    interior = latest(InteriorBuild, edition)
    spec = spec_for(edition).finalize(page_count=interior.page_count, spine_width_in=spine)
    geometry = IngramSparkAdapter.cover_geometry(spec)
    executable = shutil.which("lualatex")
    if not executable:
        raise ValueError("Instale LuaLaTeX para montar a capa.")
    build = new_build(CoverBuild, edition, interior=interior, geometry=geometry_payload(geometry))
    try:
        root = root_for(build)
        for upload, name in ((front, "FRONT.pdf"), (back, "BACK.pdf"), (template, "OFFICIAL_TEMPLATE.pdf")):
            upload_pdf(upload, root / name)
            if inspect_pdf_page_count(root / name) != 1:
                raise ValueError("Frente, contracapa e template devem ter uma página cada.")
        for name in ("FRONT.pdf", "BACK.pdf"):
            validate_pdf_page_size(
                root / name,
                width_in=spec.trim_width_in + spec.cover_bleed_in,
                height_in=spec.trim_height_in + 2 * spec.cover_bleed_in,
                label="Frente e contracapa",
            )
        validate_pdf_page_size(
            root / "OFFICIAL_TEMPLATE.pdf",
            width_in=geometry.spread_width_in,
            height_in=geometry.spread_height_in,
            label="O template oficial",
        )
        tex = root / "COVER.tex"
        tex.write_text(cover_tex(geometry), encoding="utf-8")
        result = subprocess.run(
            [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "COVER.tex"],
            cwd=root, capture_output=True, timeout=180, check=False,
        )
        pdf = root / "COVER.pdf"
        if result.returncode or not pdf.is_file() or inspect_pdf_page_count(pdf) != 1:
            raise ValueError("Não foi possível montar a capa em uma página.")
        guard(edition)
        for model in (PreflightReport, PrintExport):
            model.objects.filter(edition=edition, status="READY").update(status="INVALIDATED")
        build.tools = {"lualatex": tool_version("lualatex")}
        record(build, {"pdf": pdf, "tex": tex, "front": root / "FRONT.pdf", "back": root / "BACK.pdf",
                       "template": root / "OFFICIAL_TEMPLATE.pdf"}, PrintEdition.State.COVER_READY)
    except Exception as exc:
        fail(build, exc)
        raise
    return build
