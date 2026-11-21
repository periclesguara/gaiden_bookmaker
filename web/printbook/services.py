"""Versioned physical production. All entrypoints verify the editorial source."""
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
import math
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from django.utils.text import slugify
from PIL import Image

from django.db import transaction
from django.conf import settings
from django.utils import timezone

from gaiden.infrastructure import storage
from gaiden.print_engine.compiler import compile_lualatex
from gaiden.print_engine.image_enhancement import enhance_print_image
from gaiden.print_engine.epub_frontmatter import merge_frontmatter_with_body
from gaiden.print_engine.isbn import isbn13
from gaiden.print_engine.manuscript import manuscript_tex
from gaiden.print_engine.preflight import inspect_pdf_page_count, preflight_pdfs
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.providers.industrial import validate_industrial
from gaiden.print_engine.specs import Binding, PrintProvider, PrintSpec
from .models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport
from .trim_sizes import validate_trim


_OPENING_IMAGE_RE = re.compile(r"(?:src|href)=[\"']\.\./images/([^\"']+)[\"']", re.IGNORECASE)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_SIMULATOR_CALIPER_IN = {"white": Decimal("0.00225"), "cream": Decimal("0.00250")}
_PLATFORM_COMPATIBILITY = {
    "ingramspark": {"bindings": {"paperback", "hardcover", "hardcover_dust_jacket"}, "papers": {"white", "cream"}},
    "amazon_kdp": {"bindings": {"paperback", "hardcover"}, "papers": {"white", "cream"}},
    "uiclap": {"bindings": {"paperback"}, "papers": {"white", "cream"}},
}
_OFFICIAL_COMPOSITION_SOURCES = {
    "ingramspark": "https://www.ingramspark.com/hubfs/downloads/file-creation-guide.pdf",
    "amazon_kdp": "https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6",
    "uiclap": "https://suporte.uiclap.com/support/solutions/articles/67000747527-margens-sangrias-e-o-caminho-para-uma-impress%C3%A3o-perfeita",
}


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
            "leading": str(typography.leading_pt), "license": typography.license_reference,
            "composition": edition.composition_settings}


def provider_support(edition):
    profile = _PLATFORM_COMPATIBILITY.get(edition.platform)
    if not profile:
        raise ValueError("Fornecedor de impressão não reconhecido.")
    if edition.binding not in profile["bindings"]:
        raise ValueError("A encadernação escolhida não é compatível com este fornecedor.")
    if edition.paper not in profile["papers"]:
        raise ValueError("O papel escolhido não é compatível com este fornecedor.")
    if edition.interior_color != "black_and_white":
        raise ValueError("A composição atual aceita somente miolo em preto e branco.")
    return profile


def invalidate(edition):
    for model in (InteriorBuild, CoverBuild, PreflightReport, PrintExport):
        model.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
    edition.state = PrintEdition.State.INVALIDATED
    edition.interior_approval = {}
    edition.cover_approval = {}
    edition.visual_review_approved_at = None
    edition.edition_approved_at = None
    edition.save(update_fields=["state", "interior_approval", "cover_approval", "visual_review_approved_at", "edition_approved_at", "updated_at"])


def invalidate_after_interior(edition):
    for model in (CoverBuild, PreflightReport, PrintExport):
        model.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
    edition.interior_approval = {}
    edition.cover_approval = {}
    edition.visual_review_approved_at = None
    edition.edition_approved_at = None


def invalidate_after_cover(edition):
    for model in (PreflightReport, PrintExport):
        model.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
    edition.cover_approval = {}
    edition.visual_review_approved_at = None
    edition.edition_approved_at = None


def official_composition_preset(edition, page_count=None):
    """Return a documented provider baseline plus neutral editorial choices."""
    values = {
        "body_font": edition.typography.font,
        "font_size_pt": str(edition.typography.size_pt),
        "leading_pt": str(edition.typography.leading_pt),
        "paragraph_indent_em": "1.25",
        "inner_margin_in": "0.75",
        "outer_margin_in": "0.50",
        "top_margin_in": "0.55",
        "bottom_margin_in": "0.65",
        "header_style": "none",
        "footer_style": "page",
        "page_numbering": True,
        "chapter_opening": "recto",
    }

    if edition.platform == "ingramspark":
        values.update({
            "inner_margin_in": "0.50", "outer_margin_in": "0.50",
            "top_margin_in": "0.50", "bottom_margin_in": "0.50",
        })
        title = "IngramSpark · mínimo documentado"
        rule = "Margem recomendada mínima de 0,5″ em todos os lados para miolo preto e branco."
        basis = "recomendação oficial"
    elif edition.platform == "amazon_kdp":
        bands = ((150, "0.375"), (300, "0.50"), (500, "0.625"), (700, "0.75"), (828, "0.875"))
        resolved_pages = int(page_count) if page_count else None
        gutter = next((margin for maximum, margin in bands if resolved_pages and resolved_pages <= maximum), "0.875")
        values.update({
            "inner_margin_in": gutter, "outer_margin_in": "0.50",
            "top_margin_in": "0.50", "bottom_margin_in": "0.50",
        })
        title = "Amazon KDP · margem por paginação"
        if resolved_pages:
            rule = f"Margem interna oficial para {resolved_pages} páginas: {gutter}″; demais margens em 0,5″, acima do mínimo sem sangria."
        else:
            rule = "Antes da paginação, usa margem interna conservadora de 0,875″ (até 828 páginas); reaplique após compilar para ajustar à faixa real."
        basis = "mínimo oficial + margem externa conservadora"
    elif edition.platform == "uiclap":
        values.update({
            "inner_margin_in": "0.80", "outer_margin_in": "0.60",
            "top_margin_in": "0.60", "bottom_margin_in": "0.70",
        })
        title = "UICLAP · livro de formato médio"
        rule = "Base oficial de 2,0 cm internos e 1,5 cm externos; margens verticais editoriais seguras para leitura."
        basis = "recomendação oficial + acabamento editorial"
    else:
        raise ValueError("Fornecedor de impressão não reconhecido.")

    return {
        "provider": edition.platform, "title": title, "rule": rule,
        "basis": basis, "source_url": _OFFICIAL_COMPOSITION_SOURCES[edition.platform],
        "values": values, "page_count": page_count,
    }


def default_composition(edition):
    values = official_composition_preset(edition)["values"]
    values.update(edition.composition_settings or {})
    return values


def save_composition(edition, cleaned_data):
    normalized = {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in cleaned_data.items()
    }
    # Persist the first explicit preset even when its values match the visible
    # defaults; the compiler reads only versioned edition settings.
    if edition.composition_settings and normalized == default_composition(edition):
        return False
    InteriorBuild.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
    invalidate_after_interior(edition)
    edition.composition_settings = normalized
    edition.state = PrintEdition.State.CONFIGURED
    edition.save(update_fields=[
        "composition_settings", "state", "interior_approval", "cover_approval",
        "visual_review_approved_at", "edition_approved_at", "updated_at",
    ])
    return True


def record_configuration_history(edition, before, after):
    if before == after:
        return
    history = list(edition.configuration_history or [])
    history.append({"changed_at": timezone.now().isoformat(), "before": before, "after": after})
    edition.configuration_history = history[-50:]
    edition.save(update_fields=["configuration_history", "updated_at"])


def approve_interior(edition):
    guard(edition)
    interior = latest(InteriorBuild, edition)
    item = interior.files.get("pdf")
    if not item:
        raise ValueError("O miolo atual não possui PDF verificável.")
    edition.interior_approval = {
        "build_id": interior.id,
        "version": interior.version,
        "sha256": item["sha256"],
        "page_count": interior.page_count,
        "configuration": snapshot(edition),
        "approved_at": timezone.now().isoformat(),
    }
    edition.save(update_fields=["interior_approval", "updated_at"])
    return interior


def approve_cover(edition):
    guard(edition)
    interior = latest(InteriorBuild, edition)
    cover = latest(CoverBuild, edition)
    if edition.interior_approval.get("build_id") != interior.id:
        raise ValueError("Aprove o miolo atual antes de aprovar a capa.")
    if cover.interior_id != interior.id or not (cover.files.get("template") or cover.files.get("configured_cover")):
        raise ValueError("A capa final deve usar o miolo atual e ter geometria de produção conferida.")
    if any(item.get("severity") in {"BLOCKER", "ERROR"} for item in cover.findings):
        raise ValueError("Corrija os bloqueadores da capa antes da aprovação.")
    edition.cover_approval = {
        "build_id": cover.id,
        "version": cover.version,
        "sha256": cover.files["pdf"]["sha256"],
        "geometry": cover.geometry,
        "approved_at": timezone.now().isoformat(),
    }
    edition.save(update_fields=["cover_approval", "updated_at"])
    return cover


def approve_visual_review(edition):
    report = latest(PreflightReport, edition)
    if report.status != "READY" or any(item.get("severity") in {"BLOCKER", "ERROR"} for item in report.findings):
        raise ValueError("A revisão visual exige um preflight atual sem bloqueadores.")
    if edition.interior_approval.get("build_id") != report.interior_id or edition.cover_approval.get("build_id") != report.cover_id:
        raise ValueError("As aprovações do miolo e da capa não correspondem ao preflight atual.")
    edition.visual_review_approved_at = timezone.now()
    edition.save(update_fields=["visual_review_approved_at", "updated_at"])
    return report


def approve_edition(edition):
    report = latest(PreflightReport, edition)
    if not edition.visual_review_approved_at:
        raise ValueError("Conclua a revisão visual humana antes de aprovar a edição.")
    if any(item.get("severity") in {"BLOCKER", "ERROR"} for item in report.findings):
        raise ValueError("A edição não pode ser aprovada com bloqueadores ativos.")
    edition.edition_approved_at = timezone.now()
    edition.state = PrintEdition.State.PRINT_READY
    edition.save(update_fields=["edition_approved_at", "state", "updated_at"])
    return report


def workflow_steps(edition):
    interior = InteriorBuild.objects.filter(edition=edition, status="READY").first()
    cover = CoverBuild.objects.filter(edition=edition, status="READY").first()
    report = PreflightReport.objects.filter(edition=edition, status__in=["READY", "BLOCKED"]).first()
    exported = PrintExport.objects.filter(edition=edition, status="READY").exists()
    latest_interior = InteriorBuild.objects.filter(edition=edition).first()
    latest_cover = CoverBuild.objects.filter(edition=edition).first()
    def status(*, exists=False, approved=False, error=False):
        if error:
            return "error", "Erro"
        if approved:
            return "approved", "Aprovado"
        if exists:
            return "review", "Pronto para revisão"
        return "pending", "Não iniciado"
    configuration_status = status(
        exists=True,
        approved=edition.state not in {PrintEdition.State.DRAFT, PrintEdition.State.INVALIDATED},
        error=edition.state == PrintEdition.State.INVALIDATED,
    )
    interior_status = status(
        exists=bool(interior),
        approved=bool(interior and edition.interior_approval.get("build_id") == interior.id),
        error=bool(latest_interior and latest_interior.status == "FAILED"),
    )
    cover_status = status(
        exists=bool(cover),
        approved=bool(cover and edition.cover_approval.get("build_id") == cover.id),
        error=bool(latest_cover and latest_cover.status == "FAILED"),
    )
    if latest_cover and not cover and latest_cover.status in {"DRAFT", "BLOCKED", "RUNNING"}:
        cover_status = {
            "DRAFT": ("progress", "Em edição"),
            "BLOCKED": ("error", "Ajustes pendentes"),
            "RUNNING": ("progress", "Em processamento"),
        }[latest_cover.status]
    report_status = status(
        exists=bool(report and report.status == "READY"),
        approved=bool(edition.edition_approved_at),
        error=bool(report and report.status == "BLOCKED"),
    )
    if report and report.status == "BLOCKED":
        report_status = ("error", "Ajustes necessários")
    if getattr(edition, "cover_mode", "") == "automatic_pdf":
        return [
            {"number": "01", "label": "Configuração", "url_name": "printbook:configuration", "status": configuration_status[0], "status_label": configuration_status[1]},
            {"number": "02", "label": "Miolo", "url_name": "printbook:interior", "status": interior_status[0], "status_label": interior_status[1]},
            {"number": "03", "label": "Capa automática", "url_name": "printbook:cover", "cover_tab": "automatic", "status": cover_status[0], "status_label": cover_status[1] + " · PDF configurado"},
            {"number": "04", "label": "Preflight", "url_name": "printbook:preflight", "status": report_status[0], "status_label": report_status[1]},
            {"number": "05", "label": "Exportação", "url_name": "printbook:export", "status": "approved" if exported else "pending", "status_label": "Aprovado" if exported else "Não iniciado"},
        ]
    return [
        {"number": "01", "label": "Configuração", "url_name": "printbook:configuration", "status": configuration_status[0], "status_label": configuration_status[1]},
        {"number": "02", "label": "Miolo", "url_name": "printbook:interior", "status": interior_status[0], "status_label": interior_status[1]},
        {"number": "03", "label": "Capa", "url_name": "printbook:cover", "cover_tab": "front", "status": cover_status[0], "status_label": cover_status[1]},
        {"number": "04", "label": "Contracapa", "url_name": "printbook:cover", "cover_tab": "back", "status": cover_status[0], "status_label": cover_status[1] + " · capa completa"},
        {"number": "05", "label": "Lombada", "url_name": "printbook:cover", "cover_tab": "spine", "status": cover_status[0], "status_label": cover_status[1] + " · capa completa"},
        {"number": "06", "label": "Preflight", "url_name": "printbook:preflight", "status": report_status[0], "status_label": report_status[1]},
        {"number": "07", "label": "Exportação", "url_name": "printbook:export", "status": "approved" if exported else "pending", "status_label": "Aprovado" if exported else "Não iniciado"},
    ]


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
            expected_snapshot = snapshot(edition)
            recorded_snapshot = dict(build.snapshot or {})
            # Builds created before the workflow refactor did not store the
            # empty composition key. Treat that representation as equivalent.
            if not expected_snapshot.get("composition") and "composition" not in recorded_snapshot:
                expected_snapshot.pop("composition", None)
            if recorded_snapshot != expected_snapshot or malformed:
                invalidate(edition)
                raise ValueError("Configuração ou artefato alterado; builds invalidados. Compile novamente.")
    return path


def spec_for(edition):
    provider_support(edition)
    validate_trim(edition.platform, edition.binding, edition.paper, edition.trim)
    width, height = edition.trim.split("x")
    configured = edition.composition_settings or {}
    binding = "hardcover" if edition.binding.startswith("hardcover") else "paperback"
    return PrintSpec(
        provider=PrintProvider(edition.platform), binding=Binding(binding),
        trim_width_in=width, trim_height_in=height, paper=edition.paper,
        body_font=configured.get("body_font", edition.typography.font),
        body_font_size_pt=configured.get("font_size_pt", edition.typography.size_pt),
        body_leading_pt=configured.get("leading_pt", edition.typography.leading_pt),
        inner_margin_in=configured.get("inner_margin_in", "0.75"),
        outer_margin_in=configured.get("outer_margin_in", "0.5"),
        top_margin_in=configured.get("top_margin_in", "0.55"),
        bottom_margin_in=configured.get("bottom_margin_in", "0.65"),
    )


def is_uiclap_stapled(edition, page_count):
    """UICLAP states paperback books below 60 pages are stapled without a spine."""
    return getattr(edition, "platform", None) == "uiclap" and page_count < 60


def estimated_spine_width(edition, page_count):
    """Return a simulator-only spine estimate; provider approval still needs its template."""
    if is_uiclap_stapled(edition, page_count):
        return Decimal("0")
    coefficient = _SIMULATOR_CALIPER_IN.get(edition.paper, Decimal("0.00225"))
    return (Decimal(page_count) * coefficient).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def cover_spec_for(edition, page_count, spine):
    """Calculate stapled UICLAP proof geometry without certifying a final template."""
    spec = spec_for(edition)
    if is_uiclap_stapled(edition, page_count):
        # UICLAP describes 5 mm bleed on each edge; this remains a draft proof.
        spec = replace(spec, cover_bleed_in=Decimal("5") / Decimal("25.4"))
        spine = Decimal("0")
    return spec.finalize(page_count=page_count, spine_width_in=spine)


def estimated_cover_geometry(edition, page_count):
    spine = estimated_spine_width(edition, page_count)
    return geometry_payload(CoverGeometry.from_spec(cover_spec_for(edition, page_count, spine)))


def root_for(build):
    return storage.storage_root() / "printbook" / str(build.edition_id) / build._meta.model_name / f"v{build.version:04d}"


def verified_build_file(build, item):
    """Resolve a recorded artifact from the active or legacy repository data root."""
    if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
        raise ValueError("Metadados do artefato estão incompletos.")
    path = Path(item["path"]).resolve()
    legacy_root = (
        storage.repo_root() / "data" / "printbook" / str(build.edition_id)
        / build._meta.model_name / f"v{build.version:04d}"
    ).resolve()
    allowed_roots = {root_for(build).resolve(), legacy_root}
    if not any(path.is_relative_to(root) for root in allowed_roots):
        raise ValueError("O artefato está fora das raízes permitidas.")
    if not path.is_file() or sha(path) != item["sha256"]:
        raise ValueError("O artefato não existe ou falhou na verificação de integridade.")
    return path


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
        epub_path = edition.source._safe_existing_path(edition.source.epub_path, require_epub=True)
        manuscript = merge_frontmatter_with_body(epub_path, path.read_text(encoding="utf-8-sig"))
        tex = manuscript_tex(
            manuscript,
            spec,
            image_manifest=image_manifest,
            language_code=edition.source.language_code,
            composition=edition.composition_settings,
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
        # A new interior makes only dependent artefacts and approvals stale.
        invalidate_after_interior(edition)
        files = {"pdf": result.pdf_path, "tex": source}
        files.update(image_files)
        record(build, files, PrintEdition.State.INTERIOR_READY)
        edition.save(update_fields=["interior_approval", "cover_approval", "visual_review_approved_at", "edition_approved_at", "updated_at"])
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


def save_cover_draft_fields(edition, fields):
    """Persist editable cover fields before any PDF or image build can fail."""
    excluded = {"cover_image", "back_image", "spine_image", "template"}
    draft = dict(edition.cover_settings or {})
    for key, value in fields.items():
        if key in excluded or value is None:
            continue
        if isinstance(value, Decimal):
            value = str(value)
        if isinstance(value, (str, int, float, bool)):
            draft[key] = value
    draft["save_mode"] = "draft"
    edition.cover_settings = draft
    edition.save(update_fields=["cover_settings", "updated_at"])
    return draft


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


def _prepare_design_image(*, upload, previous, root, prefix, previous_key, profile, geometry, zoom, panel_width=None):
    """Preserve an original raster and build a versioned print derivative."""
    source_path = None
    original_dimensions = (None, None)
    if upload is not None:
        suffix = Path(upload.name).suffix.lower()
        source_path = root / f"{prefix}_ORIGINAL{suffix}"
        original_dimensions = upload_cover_image(upload, source_path)
    elif previous and previous.files.get(previous_key):
        candidate = Path(previous.files[previous_key]["path"])
        if candidate.is_file() and sha(candidate) == previous.files[previous_key]["sha256"]:
            suffix = candidate.suffix.lower()
            source_path = root / f"{prefix}_ORIGINAL{suffix}"
            shutil.copy2(candidate, source_path)
            with Image.open(source_path) as image:
                original_dimensions = image.size
    if source_path is None:
        return None
    output_path = root / f"{prefix}{source_path.suffix.lower()}"
    scale = Decimal(str(zoom)) / Decimal("100")
    output_width = panel_width or (geometry.trim_width_in + geometry.bleed_in)
    enhancement = enhance_print_image(
        source_path, output_path, profile,
        minimum_width_px=math.ceil(float(output_width * scale * Decimal("300"))),
        minimum_height_px=math.ceil(float(geometry.spread_height_in * scale * Decimal("300"))),
    )
    return {
        "name": output_path.name, "path": output_path, "original_path": source_path,
        "original_dimensions": original_dimensions,
        "output_dimensions": (enhancement["width_px"], enhancement["height_px"]),
        "enhancement": enhancement,
    }


def import_cover(edition, cover, template, spine):
    guard(edition)
    interior = latest(InteriorBuild, edition)
    spec = spec_for(edition).finalize(page_count=interior.page_count, spine_width_in=spine)
    geometry = CoverGeometry.from_spec(spec)
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


def import_automatic_cover_pdf(edition, cover_pdf):
    """Register a one-page, fully configured cover PDF for the current interior."""
    guard(edition)
    interior = latest(InteriorBuild, edition)
    if not interior or not interior.page_count or interior.page_count < 1:
        raise ValueError("Compile e aprove o miolo antes de inserir a capa automática.")
    spine = Decimal("0") if is_uiclap_stapled(edition, interior.page_count) else estimated_spine_width(edition, interior.page_count)
    spec = cover_spec_for(edition, interior.page_count, spine)
    geometry = CoverGeometry.from_spec(spec)
    build = new_build(CoverBuild, edition, interior=interior, geometry=geometry_payload(geometry))
    try:
        root = root_for(build)
        target = root / "COVER.pdf"
        upload_pdf(cover_pdf, target)
        if inspect_pdf_page_count(target) != 1:
            raise ValueError("A capa automática deve ser um PDF de uma página.")
        validate_pdf_page_size(
            target,
            width_in=geometry.spread_width_in,
            height_in=geometry.spread_height_in,
            label="A capa automática",
        )
        build.snapshot = {"source": "automatic_pdf", "configured_pdf": True, "skips_back_and_spine": True}
        build.save(update_fields=["snapshot"])
        for model in (PreflightReport, PrintExport):
            model.objects.filter(edition=edition, status="READY").update(status="INVALIDATED")
        record(build, {"pdf": target, "configured_cover": target}, PrintEdition.State.COVER_READY)
        edition.cover_mode = "automatic_pdf"
        edition.cover_approval = {"build_id": build.id, "source": "automatic_pdf", "approved_at": timezone.now().isoformat()}
        edition.save(update_fields=["cover_mode", "cover_approval", "updated_at"])
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
    language="",
    publisher="",
    front_title_x_percent=50,
    front_title_y_percent=20,
    front_author_y_percent=84,
    front_subtitle_x_percent=50,
    front_subtitle_y_percent=34,
    front_author_x_percent=50,
    front_language_x_percent=50,
    front_language_y_percent=84,
    front_text_scale_percent=100,
    front_text_align="center",
    front_publisher_y_percent=89,
    front_publisher_x_percent=50,
    front_palette="classic_blue",
    cover_font_family="TeX Gyre Pagella",
    front_title_font_family="EB Garamond",
    front_subtitle_font_family="EB Garamond",
    front_author_font_family="EB Garamond",
    front_language_font_family="EB Garamond",
    front_publisher_font_family="EB Garamond",
    front_title_color="#FFFFFF",
    front_subtitle_color="#D4A24C",
    front_author_color="#D4A24C",
    front_language_color="#D4A24C",
    front_publisher_color="#D4A24C",
    front_title_font_size_pt=28,
    front_subtitle_font_size_pt=14,
    front_author_font_size_pt=12,
    front_language_font_size_pt=8,
    front_publisher_font_size_pt=10,
    front_title_weight="bold",
    front_title_style="normal",
    front_subtitle_weight="regular",
    front_subtitle_style="normal",
    front_author_weight="regular",
    front_author_style="normal",
    front_language_weight="regular",
    front_language_style="normal",
    front_publisher_weight="regular",
    front_publisher_style="normal",
    front_title_letter_spacing_pt=0,
    front_subtitle_letter_spacing_pt=0,
    front_author_letter_spacing_pt=0,
    front_language_letter_spacing_pt=0,
    front_publisher_letter_spacing_pt=0,
    back_text,
    back_heading="",
    back_quote="",
    back_bio="",
    back_footer="",
    back_heading_font_size_pt=17,
    back_body_font_size_pt=10.5,
    back_text_align="left",
    approve_empty_back=False,
    back_text_x_percent=50,
    back_text_y_percent=12,
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
    image_offset_x_mm=0,
    image_offset_y_mm=0,
    image_rotation_deg=0,
    back_image=None,
    back_image_fit="cover",
    back_image_zoom=100,
    back_image_x=50,
    back_image_y=50,
    back_image_offset_x_mm=0,
    back_image_offset_y_mm=0,
    back_image_rotation_deg=0,
    spine_image=None,
    spine_image_fit="cover",
    spine_image_zoom=100,
    spine_image_x=50,
    spine_image_y=50,
    allow_empty_areas=False,
    minimum_dpi=300,
    spine_title=None,
    spine_author="",
    spine_publisher="",
    spine_font_family="TeX Gyre Pagella",
    spine_font_size_pt=8,
    spine_font_weight="regular",
    spine_color="#FFFFFF",
    spine_letter_spacing_pt=0,
    spine_align_horizontal="center",
    spine_align_vertical="center",
    spine_offset_x_mm=0,
    spine_offset_y_mm=0,
    spine_text_x_percent=50,
    spine_text_y_percent=50,
    spine_rotation="-90",
    spine_edge_safe_mm=1.6,
    overlay_opacity=25,
    enhancement_profile="print_safe",
    save_mode="draft",
    **back_element_fields,
):
    """Generate a local text-first cover from operator-selected design data."""
    from gaiden.print_engine.cover_composition import design_cover_tex, image_print_metrics

    guard(edition)
    interior = latest(InteriorBuild, edition)
    previous = CoverBuild.objects.filter(
        edition=edition, interior=interior, status__in=["DRAFT", "BLOCKED", "READY"]
    ).first()
    previous_design = load_cover_design(previous)
    spine_text = (spine_text or "").strip()
    if spine_title is None and not spine_text:
        spine_text = previous_design.get("spine_text") or edition.name
    effective_spine_title = spine_text if spine_title is None else spine_title.strip()
    if save_mode not in {"draft", "final"}:
        raise ValueError("Modo de salvamento da capa inválido.")
    stapled = is_uiclap_stapled(edition, interior.page_count)
    if stapled and save_mode == "final":
        raise ValueError("A capa UICLAP grampeada permanece como rascunho até existir validação do formato oficial de produção.")
    if stapled and spine_image is not None:
        raise ValueError("A UICLAP não usa lombada com menos de 60 páginas; remova a imagem da lombada antes de salvar a prova.")
    if save_mode == "final" and template is None:
        raise ValueError(f"Para salvar a capa final, anexe o template oficial da plataforma {edition.get_platform_display()}.")
    spine = Decimal("0") if stapled else (spine if spine is not None else estimated_spine_width(edition, interior.page_count))
    spec = cover_spec_for(edition, interior.page_count, spine)
    geometry = CoverGeometry.from_spec(spec)
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
        front_art = _prepare_design_image(
            upload=cover_image, previous=previous, root=root, prefix="COVER_IMAGE",
            previous_key="cover_image_original", profile=enhancement_profile,
            geometry=geometry, zoom=image_zoom,
        )
        back_art = _prepare_design_image(
            upload=back_image, previous=previous, root=root, prefix="BACK_IMAGE",
            previous_key="back_image_original", profile=enhancement_profile,
            geometry=geometry, zoom=back_image_zoom,
        )
        spine_art = None if stapled else _prepare_design_image(
            upload=spine_image, previous=previous, root=root, prefix="SPINE_IMAGE",
            previous_key="spine_image_original", profile=enhancement_profile,
            geometry=geometry, zoom=spine_image_zoom, panel_width=geometry.spine_width_in,
        )
        design = {
            "layout": layout,
            "add_front_text": add_front_text,
            "title": title,
            "subtitle": subtitle,
            "author": author,
            "language": language,
            "publisher": publisher,
            "front_title_x_percent": int(front_title_x_percent),
            "front_title_y_percent": int(front_title_y_percent),
            "front_author_y_percent": int(front_author_y_percent),
            "front_subtitle_x_percent": int(front_subtitle_x_percent),
            "front_subtitle_y_percent": int(front_subtitle_y_percent),
            "front_author_x_percent": int(front_author_x_percent),
            "front_language_x_percent": int(front_language_x_percent),
            "front_language_y_percent": int(front_language_y_percent),
            "front_text_scale_percent": int(front_text_scale_percent),
            "front_text_align": front_text_align,
            "front_publisher_y_percent": int(front_publisher_y_percent),
            "front_publisher_x_percent": int(front_publisher_x_percent),
            "front_palette": front_palette,
            "cover_font_family": cover_font_family,
            "front_title_font_family": front_title_font_family,
            "front_subtitle_font_family": front_subtitle_font_family,
            "front_author_font_family": front_author_font_family,
            "front_language_font_family": front_language_font_family,
            "front_publisher_font_family": front_publisher_font_family,
            "front_title_color": front_title_color,
            "front_subtitle_color": front_subtitle_color,
            "front_author_color": front_author_color,
            "front_language_color": front_language_color,
            "front_publisher_color": front_publisher_color,
            "front_title_font_size_pt": str(front_title_font_size_pt),
            "front_subtitle_font_size_pt": str(front_subtitle_font_size_pt),
            "front_author_font_size_pt": str(front_author_font_size_pt),
            "front_language_font_size_pt": str(front_language_font_size_pt),
            "front_publisher_font_size_pt": str(front_publisher_font_size_pt),
            "front_title_weight": front_title_weight,
            "front_title_style": front_title_style,
            "front_subtitle_weight": front_subtitle_weight,
            "front_subtitle_style": front_subtitle_style,
            "front_author_weight": front_author_weight,
            "front_author_style": front_author_style,
            "front_language_weight": front_language_weight,
            "front_language_style": front_language_style,
            "front_publisher_weight": front_publisher_weight,
            "front_publisher_style": front_publisher_style,
            "front_title_letter_spacing_pt": str(front_title_letter_spacing_pt),
            "front_subtitle_letter_spacing_pt": str(front_subtitle_letter_spacing_pt),
            "front_author_letter_spacing_pt": str(front_author_letter_spacing_pt),
            "front_language_letter_spacing_pt": str(front_language_letter_spacing_pt),
            "front_publisher_letter_spacing_pt": str(front_publisher_letter_spacing_pt),
            "back_text": back_text,
            "back_heading": back_heading,
            "back_quote": back_quote,
            "back_bio": back_bio,
            "back_footer": back_footer,
            "back_heading_font_size_pt": str(back_heading_font_size_pt),
            "back_body_font_size_pt": str(back_body_font_size_pt),
            "back_text_align": back_text_align,
            "approve_empty_back": bool(approve_empty_back),
            "back_text_x_percent": int(back_text_x_percent),
            "back_text_y_percent": int(back_text_y_percent),
            "spine_text": spine_text,
            "spine_title": effective_spine_title,
            "spine_author": spine_author,
            "spine_publisher": spine_publisher,
            "spine_font_family": spine_font_family,
            "spine_font_size_pt": str(spine_font_size_pt),
            "spine_font_weight": spine_font_weight,
            "spine_color": spine_color,
            "spine_letter_spacing_pt": str(spine_letter_spacing_pt),
            "spine_align_horizontal": spine_align_horizontal,
            "spine_align_vertical": spine_align_vertical,
            "spine_offset_x_mm": str(spine_offset_x_mm),
            "spine_offset_y_mm": str(spine_offset_y_mm),
            "spine_text_x_percent": int(spine_text_x_percent),
            "spine_text_y_percent": int(spine_text_y_percent),
            "spine_rotation": str(spine_rotation),
            "spine_edge_safe_mm": str(spine_edge_safe_mm),
            "background_color": background_color,
            "text_color": text_color,
            "accent_color": accent_color,
            "back_palette": back_palette,
            "back_background_color": back_background_color,
            "back_text_color": back_text_color,
            "back_accent_color": back_accent_color,
            "page_count": interior.page_count,
            "spine_width_in": str(spine),
            "geometry_source": "uiclap_stapled_draft" if stapled else ("official_template" if template_path else (
                "kdp_official_page_caliper_formula" if edition.platform == "amazon_kdp" else "explicit_simulator_estimate"
            )),
            "image_fit": image_fit,
            "image_zoom": image_zoom,
            "image_x": image_x,
            "image_y": image_y,
            "image_offset_x_mm": str(image_offset_x_mm),
            "image_offset_y_mm": str(image_offset_y_mm),
            "image_rotation_deg": str(image_rotation_deg),
            "back_image_fit": back_image_fit,
            "back_image_zoom": back_image_zoom,
            "back_image_x": back_image_x,
            "back_image_y": back_image_y,
            "back_image_offset_x_mm": str(back_image_offset_x_mm),
            "back_image_offset_y_mm": str(back_image_offset_y_mm),
            "back_image_rotation_deg": str(back_image_rotation_deg),
            "spine_image_fit": spine_image_fit,
            "spine_image_zoom": spine_image_zoom,
            "spine_image_x": spine_image_x,
            "spine_image_y": spine_image_y,
            "allow_empty_areas": bool(allow_empty_areas),
            "minimum_dpi": int(minimum_dpi),
            "overlay_opacity": overlay_opacity,
            "enhancement_profile": enhancement_profile,
            "image_enhancement": front_art["enhancement"] if front_art else None,
            "back_image_enhancement": back_art["enhancement"] if back_art else None,
            "spine_image_enhancement": spine_art["enhancement"] if spine_art else None,
            "barcode_mode": edition.barcode_mode,
            "save_mode": save_mode,
        }
        for key, value in back_element_fields.items():
            if not key.startswith("back_element_"):
                raise ValueError("Campo de capa desconhecido.")
            if value is not None:
                design[key] = str(value) if isinstance(value, Decimal) else value
        design_findings = []
        cover_copy = (title, subtitle, author, language, publisher, back_heading, back_quote, back_text, back_bio, back_footer, effective_spine_title, spine_author, spine_publisher)
        if any("**" in (part or "") or "__" in (part or "") for part in cover_copy):
            design_findings.append({"code": "cover.markdown_literal", "severity": "WARNING",
                "message": "O texto contém marcas de Markdown que serão impressas literalmente. Revise os campos antes da prova final."})
        if stapled:
            design_findings.append({"code": "cover.uiclap_stapled_proof", "severity": "WARNING",
                "message": "UICLAP: abaixo de 60 páginas o acabamento é grampeado, sem lombada. Esta capa é apenas uma prova em rascunho."})
        for label, key, art, fit, zoom, print_width in (
            ("frente", "front_image", front_art, image_fit, image_zoom, geometry.trim_width_in + geometry.bleed_in),
            ("contracapa", "back_image", back_art, back_image_fit, back_image_zoom, geometry.trim_width_in + geometry.bleed_in),
            ("lombada", "spine_image", spine_art, spine_image_fit, spine_image_zoom, geometry.spine_width_in),
        ):
            if not art:
                continue
            if art["enhancement"]["resized"]:
                design_findings.append({
                    "code": f"cover.{key}_upscaled", "severity": "WARNING",
                    "message": f"A imagem da {label} recebeu ampliação de produção; verifique detalhes finos na prova em 100%.",
                })
            metrics = image_print_metrics(
                art["original_dimensions"][0], art["original_dimensions"][1],
                print_width, geometry.spread_height_in,
                fit=fit, scale_percent=zoom,
            )
            evidence = {
                "original_width_px": art["original_dimensions"][0],
                "original_height_px": art["original_dimensions"][1],
                "printed_width_in": str(metrics["printed_width_in"]),
                "printed_height_in": str(metrics["printed_height_in"]),
                "effective_dpi_x": int(metrics["effective_dpi_x"]),
                "effective_dpi_y": int(metrics["effective_dpi_y"]),
                "distorted": False,
            }
            design[f"{key}_quality"] = evidence
            if key == "front_image":
                design["effective_image_ppi"] = min(evidence["effective_dpi_x"], evidence["effective_dpi_y"])
            if min(evidence["effective_dpi_x"], evidence["effective_dpi_y"]) < int(minimum_dpi):
                design_findings.append({
                    "code": f"cover.{key}_resolution", "severity": "BLOCKER",
                    "message": (
                        f"A imagem original da {label} fica em {min(evidence['effective_dpi_x'], evidence['effective_dpi_y'])} DPI efetivos; o mínimo é {minimum_dpi}. "
                        + ("A ampliação interpolada não recupera detalhes da origem; use uma imagem original maior." if art["enhancement"]["resized"] else "Use uma imagem original maior ou reduza seu tamanho impresso.")
                    ),
                })
            if fit == "contain" and int(zoom) <= 100 and not allow_empty_areas:
                design_findings.append({
                    "code": f"cover.{key}_empty_area", "severity": "BLOCKER",
                    "message": f"O modo Ajustar da {label} pode deixar área vazia. Use Preencher ou marque a área vazia como intencional.",
                })
        spine_copy = " · ".join(part.strip() for part in (effective_spine_title, spine_author, spine_publisher) if part and part.strip())
        if spine_copy and not geometry.spine_text_allowed:
            design_findings.append({
                "code": "cover.spine_text_not_allowed",
                "severity": "WARNING" if stapled else ("BLOCKER" if (spine_title or spine_author or spine_publisher) else "WARNING"),
                "message": "A prova grampeada não possui lombada; o texto permanece salvo, mas não será renderizado." if stapled else "Este perfil não permite texto na lombada para a paginação atual; o texto não será exportado.",
            })
        available_spine_pt = (geometry.spine_width_in - Decimal(str(spine_edge_safe_mm)) * 2 / Decimal("25.4")) * 72
        if spine_copy and geometry.spine_text_allowed and (available_spine_pt <= 0 or Decimal(str(spine_font_size_pt)) > available_spine_pt):
            design_findings.append({
                "code": "cover.spine_text_width", "severity": "BLOCKER",
                "message": "A tipografia não cabe entre as margens de segurança da lombada.",
            })
        estimated_length_pt = Decimal(len(spine_copy)) * Decimal(str(spine_font_size_pt)) * Decimal("0.55")
        estimated_length_pt += max(0, len(spine_copy) - 1) * Decimal(str(spine_letter_spacing_pt))
        available_length_pt = (geometry.trim_height_in - Decimal("0.50")) * 72
        if spine_copy and geometry.spine_text_allowed and estimated_length_pt > available_length_pt:
            design_findings.append({
                "code": "cover.spine_text_length", "severity": "BLOCKER",
                "message": "O conjunto título/autor/editora é longo demais para a altura segura da lombada.",
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
                language=language,
                publisher=publisher,
                front_title_x_percent=front_title_x_percent,
                front_title_y_percent=front_title_y_percent,
                front_author_y_percent=front_author_y_percent,
                front_subtitle_x_percent=front_subtitle_x_percent,
                front_subtitle_y_percent=front_subtitle_y_percent,
                front_author_x_percent=front_author_x_percent,
                front_language_x_percent=front_language_x_percent,
                front_language_y_percent=front_language_y_percent,
                front_text_scale_percent=front_text_scale_percent,
                front_text_align=front_text_align,
                front_publisher_y_percent=front_publisher_y_percent,
                front_publisher_x_percent=front_publisher_x_percent,
                cover_font_family=cover_font_family,
                front_title_font_family=front_title_font_family,
                front_subtitle_font_family=front_subtitle_font_family,
                front_author_font_family=front_author_font_family,
                front_language_font_family=front_language_font_family,
                front_publisher_font_family=front_publisher_font_family,
                front_title_color=front_title_color,
                front_subtitle_color=front_subtitle_color,
                front_author_color=front_author_color,
                front_language_color=front_language_color,
                front_publisher_color=front_publisher_color,
                front_title_font_size_pt=front_title_font_size_pt,
                front_subtitle_font_size_pt=front_subtitle_font_size_pt,
                front_author_font_size_pt=front_author_font_size_pt,
                front_language_font_size_pt=front_language_font_size_pt,
                front_publisher_font_size_pt=front_publisher_font_size_pt,
                front_title_weight=front_title_weight,
                front_title_style=front_title_style,
                front_subtitle_weight=front_subtitle_weight,
                front_subtitle_style=front_subtitle_style,
                front_author_weight=front_author_weight,
                front_author_style=front_author_style,
                front_language_weight=front_language_weight,
                front_language_style=front_language_style,
                front_publisher_weight=front_publisher_weight,
                front_publisher_style=front_publisher_style,
                front_title_letter_spacing_pt=front_title_letter_spacing_pt,
                front_subtitle_letter_spacing_pt=front_subtitle_letter_spacing_pt,
                front_author_letter_spacing_pt=front_author_letter_spacing_pt,
                front_language_letter_spacing_pt=front_language_letter_spacing_pt,
                front_publisher_letter_spacing_pt=front_publisher_letter_spacing_pt,
                back_text=back_text,
                back_heading=back_heading,
                back_quote=back_quote,
                back_bio=back_bio,
                back_footer=back_footer,
                back_elements=back_element_fields,
                back_heading_font_size_pt=back_heading_font_size_pt,
                back_body_font_size_pt=back_body_font_size_pt,
                back_text_align=back_text_align,
                back_text_x_percent=back_text_x_percent,
                back_text_y_percent=back_text_y_percent,
                spine_text=spine_text,
                background_color=background_color,
                text_color=text_color,
                accent_color=accent_color,
                back_background_color=back_background_color,
                back_text_color=back_text_color,
                back_accent_color=back_accent_color,
                layout=layout,
                image_name=front_art["name"] if front_art else None,
                image_width_px=front_art["output_dimensions"][0] if front_art else None,
                image_height_px=front_art["output_dimensions"][1] if front_art else None,
                image_fit=image_fit,
                image_zoom=image_zoom,
                image_x=image_x,
                image_y=image_y,
                image_offset_x_mm=image_offset_x_mm,
                image_offset_y_mm=image_offset_y_mm,
                image_rotation_deg=image_rotation_deg,
                back_image_name=back_art["name"] if back_art else None,
                back_image_width_px=back_art["output_dimensions"][0] if back_art else None,
                back_image_height_px=back_art["output_dimensions"][1] if back_art else None,
                back_image_fit=back_image_fit,
                back_image_zoom=back_image_zoom,
                back_image_x=back_image_x,
                back_image_y=back_image_y,
                back_image_offset_x_mm=back_image_offset_x_mm,
                back_image_offset_y_mm=back_image_offset_y_mm,
                back_image_rotation_deg=back_image_rotation_deg,
                spine_image_name=spine_art["name"] if spine_art else None,
                spine_image_width_px=spine_art["output_dimensions"][0] if spine_art else None,
                spine_image_height_px=spine_art["output_dimensions"][1] if spine_art else None,
                spine_image_fit=spine_image_fit,
                spine_image_zoom=spine_image_zoom,
                spine_image_x=spine_image_x,
                spine_image_y=spine_image_y,
                spine_title=spine_title,
                spine_author=spine_author,
                spine_publisher=spine_publisher,
                spine_font_family=spine_font_family,
                spine_font_size_pt=spine_font_size_pt,
                spine_font_weight=spine_font_weight,
                spine_color=spine_color,
                spine_letter_spacing_pt=spine_letter_spacing_pt,
                spine_align_horizontal=spine_align_horizontal,
                spine_align_vertical=spine_align_vertical,
                spine_offset_x_mm=spine_offset_x_mm,
                spine_offset_y_mm=spine_offset_y_mm,
                spine_text_x_percent=spine_text_x_percent,
                spine_text_y_percent=spine_text_y_percent,
                spine_rotation=spine_rotation,
                spine_edge_safe_mm=spine_edge_safe_mm,
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
        compiler_output = b""
        for stream in (result.stdout, result.stderr):
            if isinstance(stream, str):
                stream = stream.encode("utf-8", errors="replace")
            compiler_output += stream or b""
        compiler_log = root / "COVER.log"
        if compiler_log.is_file():
            if compiler_log.stat().st_size > 8 * 1024 * 1024:
                design_findings.append({"code": "cover.font_log_unchecked", "severity": "BLOCKER",
                    "message": "O relatório de fontes é grande demais para conferência automática; a capa não pode ser aprovada."})
            else:
                compiler_output += compiler_log.read_bytes()
        if b"Missing character:" in compiler_output:
            design_findings.append({"code": "cover.missing_glyphs", "severity": "BLOCKER",
                "message": "A fonte selecionada não contém todos os caracteres da capa. Escolha uma fonte compatível e gere outra prova."})
        compiled_pdf.replace(pdf)
        guard(edition)
        build.tools = {
            "lualatex": tool_version("lualatex"),
            "designer": "gaiden-cover-studio-v3",
            "image_engine": front_art["enhancement"]["engine"] if front_art else (
                back_art["enhancement"]["engine"] if back_art else (
                    spine_art["enhancement"]["engine"] if spine_art else None
                )
            ),
        }
        build.findings = design_findings
        files = {"pdf": pdf, "tex": tex, "design": design_path}
        if template_path:
            files["template"] = template_path
        if front_art:
            files["cover_image"] = front_art["path"]
            files["cover_image_original"] = front_art["original_path"]
        if back_art:
            files["back_image"] = back_art["path"]
            files["back_image_original"] = back_art["original_path"]
        if spine_art:
            files["spine_image"] = spine_art["path"]
            files["spine_image_original"] = spine_art["original_path"]
        has_blocker = any(item["severity"] == "BLOCKER" for item in design_findings)
        invalidate_after_cover(edition)
        edition.cover_settings = design
        if save_mode == "final" and template_path and not has_blocker:
            record(build, files, PrintEdition.State.COVER_READY)
            edition.save(update_fields=["cover_settings", "cover_approval", "visual_review_approved_at", "edition_approved_at", "updated_at"])
        else:
            build.files = {key: {"path": str(path), "sha256": sha(path)} for key, path in files.items()}
            build.status = "BLOCKED" if save_mode == "final" else "DRAFT"
            build.findings = design_findings
            if not template_path:
                build.findings.append({
                    "code": "cover.template_pending",
                    "severity": "WARNING",
                    "message": "Prova UICLAP grampeada, sem lombada; ainda não é um arquivo final de produção." if stapled else "Prova visual gerada com lombada estimada. Anexe o template oficial antes do preflight.",
                })
            build.save()
            edition.state = PrintEdition.State.COVER_PENDING
            edition.save(update_fields=["state", "cover_settings", "cover_approval", "visual_review_approved_at", "edition_approved_at", "updated_at"])
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def quality_readiness(edition, interior, cover, *, source_error=""):
    """Describe prerequisites without changing builds or claiming a preflight pass."""
    checks = []
    if source_error:
        checks.append({"ok": False, "message": "A fonte editorial precisa ser conferida antes da validação.", "url_name": "printbook:configuration", "label": "Revisar configuração"})
    interior_approved = bool(interior and edition.interior_approval.get("build_id") == interior.id)
    checks.append({"ok": interior_approved, "message": "Miolo atual aprovado." if interior_approved else "Compile e aprove o miolo atual.", "url_name": "printbook:interior", "label": "Abrir miolo"})
    cover_current = bool(cover and interior and cover.interior_id == interior.id)
    automatic_cover = bool(cover_current and cover.files.get("configured_cover"))
    cover_ready = bool(cover_current and (cover.files.get("template") or automatic_cover))
    checks.append({"ok": cover_ready, "message": "Capa automática configurada e validada." if automatic_cover else ("Capa final com template oficial registrada." if cover_ready else "Salve uma capa final para o miolo atual com o template oficial; a prova em rascunho não é uma capa final."), "url_name": "printbook:cover", "label": "Abrir capa"})
    cover_approved = bool(cover_ready and edition.cover_approval.get("build_id") == cover.id)
    checks.append({"ok": cover_approved, "message": "Capa final atual aprovada." if cover_approved else "Aprove a capa final atual depois de corrigir seus bloqueadores.", "url_name": "printbook:cover", "label": "Abrir capa"})
    configured = bool(getattr(settings, "PRINTBOOK_VALIDATOR", ""))
    checks.append({"ok": False, "message": "Validador industrial configurado; sua execução e evidências serão verificadas no preflight." if configured else "Validador industrial não configurado; a aprovação para impressão ficará bloqueada até existir evidência válida.", "url_name": "printbook:preflight", "label": "Ver preflight"})
    return checks


def preflight(edition):
    guard(edition)
    interior = InteriorBuild.objects.filter(edition=edition, status="READY").first()
    if not interior:
        raise ValueError("Compile e aprove o miolo atual antes do preflight.")
    if edition.interior_approval.get("build_id") != interior.id:
        raise ValueError("Aprove o miolo atual antes de executar o controle de qualidade.")
    cover = CoverBuild.objects.filter(
        edition=edition, status__in=["READY", "DRAFT", "BLOCKED"], interior=interior
    ).first()
    if not cover:
        raise ValueError("Salve uma prova da capa atual antes de executar o controle de qualidade.")
    if cover.interior_id != interior.id:
        raise ValueError("A capa não pertence ao miolo atual.")
    geometry_evidence = cover.files.get("template") or cover.files.get("configured_cover")
    production_ready = bool(
        cover.status == "READY"
        and geometry_evidence
        and edition.cover_approval.get("build_id") == cover.id
    )
    build = new_build(PreflightReport, edition, interior=interior, cover=cover)
    try:
        spec = cover_spec_for(edition, interior.page_count, cover.geometry["spine_width_in"])
        report = preflight_pdfs(spec, interior_pdf=interior.files["pdf"]["path"], cover_pdf=cover.files["pdf"]["path"])
        build.findings = []
        for check in report.checks:
            if check.code == "pdfx.validation":
                continue
            severity = "INFO" if check.status.value == "pass" else "BLOCKER"
            message = check.message
            if check.code == "interior.exists":
                message = "PDF do miolo localizado." if check.status.value == "pass" else "PDF do miolo não foi localizado."
            elif check.code == "cover.exists":
                message = "PDF da capa localizado." if check.status.value == "pass" else "PDF da capa não foi localizado."
            elif check.code == "spec.provider_policy" and not production_ready:
                severity = "WARNING"
                message = "A validação industrial do fornecedor ainda não está configurada; a análise local da prova foi executada."
            build.findings.append({"code": check.code, "severity": severity, "message": message})
        build.findings.extend(cover.findings)
        hashes = {"interior": interior.files["pdf"]["sha256"], "cover": cover.files["pdf"]["sha256"]}
        if production_ready:
            hashes["template"] = geometry_evidence["sha256"]
            job = root_for(build) / "validation-job.json"
            job.write_text(json.dumps({"hashes": hashes, "configuration": snapshot(edition), "geometry": cover.geometry,
                "interior": interior.files["pdf"]["path"], "cover": cover.files["pdf"]["path"],
                "template": geometry_evidence["path"], "page_count": interior.page_count}, indent=2), encoding="utf-8")
            evidence = validate_industrial(getattr(settings, "PRINTBOOK_VALIDATOR", ""), job, hashes)
            build.findings.extend(evidence["checks"])
            build.tools = {
                "pdfinfo": tool_version("pdfinfo"),
                "pdffonts": tool_version("pdffonts"),
                "industrial_validator": evidence["tool_version"],
                "pdfx_standard": evidence["pdfx_standard"],
            }
        else:
            reason = (
                "A capa final ainda não foi aprovada. O controle de qualidade foi executado, mas a exportação permanece bloqueada."
                if geometry_evidence else
                "Controle de qualidade executado sobre a prova atual. Anexe o template oficial, salve a capa final e aprove-a para liberar a produção."
            )
            build.findings.append({"code": "cover.proof_only", "severity": "BLOCKER", "message": reason})
            build.tools = {
                "pdfinfo": tool_version("pdfinfo"),
                "pdffonts": tool_version("pdffonts"),
                "industrial_validator": None,
                "pdfx_standard": None,
            }
        if not edition.isbn:
            build.findings.append({"code": "isbn", "severity": "BLOCKER", "message": "Informe o ISBN desta edição física."})
        elif isbn13(edition.isbn):
            build.findings.append({"code": "isbn", "severity": "INFO", "message": "Dígito ISBN-13 válido; titularidade depende do cadastro editorial."})
        output = root_for(build) / "preflight.json"
        output.write_text(json.dumps({
            "edition_id": edition.id,
            "mode": "production_preflight" if production_ready else "proof_quality_control",
            "configuration": snapshot(edition),
            "hashes": hashes,
            "tools": build.tools,
            "checks": build.findings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        guard(edition)
        passed = not any(f["severity"] in {"BLOCKER", "ERROR"} for f in build.findings)
        edition.visual_review_approved_at = None
        edition.edition_approved_at = None
        PrintExport.objects.filter(edition=edition).exclude(status="FAILED").update(status="INVALIDATED")
        record(build, {"report": output}, PrintEdition.State.PREFLIGHT_PENDING)
        edition.save(update_fields=["visual_review_approved_at", "edition_approved_at", "updated_at"])
        if not passed:
            build.status = "BLOCKED"
            build.save(update_fields=["status"])
    except Exception as exc:
        fail(build, exc)
        raise
    return build


def approve_automatic_montage(edition):
    """Run local quality checks and bind an editorial approval to a stapled proof."""
    guard(edition)
    interior = InteriorBuild.objects.filter(edition=edition, status="READY").first()
    if not interior or edition.interior_approval.get("build_id") != interior.id:
        raise ValueError("Aprove o miolo atual antes de aprovar a montagem automática.")
    if not is_uiclap_stapled(edition, interior.page_count):
        raise ValueError("A aprovação automática sem lombada vale somente para UICLAP abaixo de 60 páginas.")
    report = preflight(edition)
    cover = report.cover
    approval = {
        "cover_build_id": cover.id,
        "interior_build_id": interior.id,
        "report_id": report.id,
        "cover_sha256": cover.files["pdf"]["sha256"],
        "approved_at": timezone.now().isoformat(),
    }
    cover_settings = dict(edition.cover_settings or {})
    cover_settings["automatic_montage_approval"] = approval
    edition.cover_settings = cover_settings
    edition.save(update_fields=["cover_settings", "updated_at"])
    return report


def export_package(edition):
    guard(edition)
    report = latest(PreflightReport, edition)
    if edition.state not in {PrintEdition.State.PRINT_READY, PrintEdition.State.EXPORTED} or not edition.edition_approved_at or any(f["severity"] in {"ERROR", "BLOCKER"} for f in report.findings):
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


def interior_chapters(build):
    """Extract a bounded, read-only chapter index from the compiled PDF."""
    item = build.files.get("pdf") if build else None
    if not item or not shutil.which("pdftotext"):
        return []
    path = Path(item["path"])
    if not path.is_file() or sha(path) != item["sha256"]:
        return []
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    chapters = []
    seen = set()
    pattern = re.compile(
        r"^\s*((?:chapter|cap[ií]tulo)\s+[0-9ivxlcdm]+(?:\s*[:.\-–—].*)?|"
        r"[ivxlcdm]+\s*[:.\-–—]\s*.+)\s*$",
        re.I,
    )
    for page_number, text in enumerate(result.stdout.split("\f"), 1):
        page_lines = text.splitlines()[:35]
        if any(line.strip().casefold() in {"sumario", "sumário"} for line in page_lines):
            continue
        for line in page_lines:
            match = pattern.match(line)
            if not match:
                continue
            label = " ".join(match.group(1).split())[:120]
            key = label.casefold()
            if key not in seen:
                seen.add(key)
                chapters.append({"label": label, "page": page_number})
            break
    return chapters[:250]


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
    geometry = CoverGeometry.from_spec(spec)
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
