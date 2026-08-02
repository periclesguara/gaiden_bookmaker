from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import (
    ArtifactReference,
    EditorialEditionPackage,
    EditorialImportPackage,
    ValidatedEditorialPackage,
    pipeline_language,
)


BOOK_CODE_RE = re.compile(r"^book_[0-9]{4,}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
PACKAGE_TYPE = "gaiden.editorial_import"
REVIEW_STATUS = "TEXTS_READY_FOR_EDITORIAL_REVIEW"

CANONICAL_TOP_LEVEL = {"schema_version", "package_type", "book", "source", "editions", "status", "incremental"}
CANONICAL_BOOK_ALLOWED = {
    "book_code",
    "title",
    "author_name",
    "source_language",
    "original_publication_date",
    "original_author_death_date",
    "work_kind",
    "publisher",
}
CANONICAL_SOURCE_ALLOWED = {"original_name", "file_type", "path", "size", "sha256", "status", "intake_item_id"}
LEGACY_TOP_LEVEL = {
    "schema_version",
    "mode",
    "pilot",
    "book_code",
    "source",
    "editions",
    "editorial_policy",
    "rights_policy",
    "gaiden_blockers_resolved",
    "pending_stages",
    "incremental_import",
    "status",
}
LEGACY_EDITION_ALLOWED = {
    "book_code",
    "author_name",
    "publication_year",
    "original_publication_date",
    "original_author_death_date",
    "work_kind",
    "imprint_name",
    "collection_name",
    "seal_name",
    "editor_name",
    "editorial_name",
    "edition_year",
    "edition_copyright_holder",
    "cover_filepath",
    "images_dir",
    "has_preface",
    "preface_text",
    "has_introduction",
    "introduction_text",
    "has_epilogue",
    "epilogue_text",
    "text_source_mode",
    "registration_status",
    "source_file_type",
    "source_original_name",
    "source_saved_path",
    "source_file_size",
    "source_uploaded_at",
    "source_file_sha256",
    "source_uploaded_by",
    "language",
    "language_variant",
    "title",
    "subtitle",
    "collaborator_name",
    "collaborator_pseudonym",
    "collaborator_roles",
    "translator_name",
    "adapter_name",
    "frontispiece_text",
    "copyright_text",
    "about_edition_text",
    "about_contributor_text",
    "end_marker",
    "table_of_contents",
    "text_output",
}
METADATA_FIELDS = {
    "title",
    "subtitle",
    "author_name",
    "publication_year",
    "original_publication_date",
    "original_author_death_date",
    "work_kind",
    "imprint_name",
    "collection_name",
    "seal_name",
    "editor_name",
    "editorial_name",
    "edition_year",
    "edition_copyright_holder",
    "cover_filepath",
    "images_dir",
    "collaborator_name",
    "collaborator_pseudonym",
    "collaborator_roles",
    "translator_name",
    "adapter_name",
    "text_source_mode",
    "registration_status",
    "source_file_type",
    "source_original_name",
    "source_saved_path",
    "source_file_size",
    "source_uploaded_at",
    "source_file_sha256",
    "source_uploaded_by",
}
FRONTMATTER_FIELDS = {
    "frontispiece_text",
    "copyright_text",
    "about_edition_text",
    "about_contributor_text",
    "has_preface",
    "preface_text",
    "has_introduction",
    "introduction_text",
    "has_epilogue",
    "epilogue_text",
}


class EditorialPackageValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink():
        raise EditorialPackageValidationError([f"Arquivo JSON não pode ser symlink: {path.name}"])
    try:
        data = path.read_bytes()
        payload = json.loads(data.decode("utf-8"))
    except FileNotFoundError as exc:
        raise EditorialPackageValidationError([f"Pacote não encontrado: {path}"]) from exc
    except UnicodeDecodeError as exc:
        raise EditorialPackageValidationError(["O pacote deve usar UTF-8."]) from exc
    except json.JSONDecodeError as exc:
        raise EditorialPackageValidationError([f"JSON inválido: linha {exc.lineno}, coluna {exc.colno}."]) from exc
    if not isinstance(payload, dict):
        raise EditorialPackageValidationError(["A raiz do pacote deve ser um objeto JSON."])
    return payload, data


def _artifact(payload: dict[str, Any], *, path_key: str = "path", hash_key: str = "sha256", size_key: str = "size", status_key: str = "status") -> ArtifactReference | None:
    path = str(payload.get(path_key) or "").strip()
    digest = str(payload.get(hash_key) or "").strip()
    if not path and not digest:
        return None
    size = payload.get(size_key)
    return ArtifactReference(path=path, sha256=digest, size=size if isinstance(size, int) else None, status=str(payload.get(status_key) or ""))


class LegacyEditorialPackageV1Adapter:
    """Explicit boundary adapter for the flat package emitted by Automated Intake."""

    @classmethod
    def adapt(cls, payload: dict[str, Any]) -> EditorialImportPackage:
        unexpected = sorted(set(payload) - LEGACY_TOP_LEVEL)
        if unexpected:
            raise EditorialPackageValidationError(["Campos legados não permitidos: " + ", ".join(unexpected)])
        editions_raw = payload.get("editions")
        if not isinstance(editions_raw, list) or not editions_raw:
            raise EditorialPackageValidationError(["editions deve conter ao menos uma edição."])
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        editions: list[EditorialEditionPackage] = []
        for index, raw in enumerate(editions_raw):
            if not isinstance(raw, dict):
                raise EditorialPackageValidationError([f"editions[{index}] deve ser um objeto."])
            unknown = sorted(set(raw) - LEGACY_EDITION_ALLOWED)
            if unknown:
                raise EditorialPackageValidationError([f"editions[{index}] contém campos não permitidos: {', '.join(unknown)}"])
            locale = str(raw.get("language_variant") or "")
            language = str(raw.get("language") or "")
            output = raw.get("text_output") if isinstance(raw.get("text_output"), dict) else {}
            body = _artifact(output, path_key="body_path", hash_key="body_sha256", status_key="body_status")
            if body is not None and body.status != "OFFICIAL":
                body = ArtifactReference(path=body.path, sha256=body.sha256, size=body.size, status="DRAFT")
            metadata = {name: raw.get(name) for name in METADATA_FIELDS if name in raw}
            frontmatter = {name: raw.get(name) for name in FRONTMATTER_FIELDS if name in raw}
            editions.append(
                EditorialEditionPackage(
                    language=language,
                    locale=locale,
                    metadata=metadata,
                    frontmatter=frontmatter,
                    body=body,
                    validation={"status": "PASS" if "QA_PASS" in str(output.get("body_status") or "") else "NOT_RUN"},
                )
            )
        first = editions_raw[0]
        source_ref = ArtifactReference(
            path=str(source.get("source_path") or first.get("source_saved_path") or ""),
            sha256=str(source.get("source_sha256") or first.get("source_file_sha256") or ""),
            size=first.get("source_file_size") if isinstance(first.get("source_file_size"), int) else None,
            status=str(source.get("source_status") or ""),
        )
        author_name = str(source.get("author") or first.get("author_name") or "").strip()
        source_title = str(source.get("title") or first.get("title") or "").strip()
        author_prefix = f"{author_name}-"
        if author_name and source_title.casefold().startswith(author_prefix.casefold()):
            source_title = source_title[len(author_prefix) :].strip()
        book = {
            "book_code": payload.get("book_code") or source.get("book_code"),
            "title": source_title,
            "author_name": author_name,
            "source_language": source.get("source_language") or "",
            "original_publication_date": first.get("original_publication_date"),
            "original_author_death_date": first.get("original_author_death_date"),
            "work_kind": first.get("work_kind") or "AUTHORIAL",
            "publisher": first.get("editorial_name") or first.get("imprint_name") or "",
        }
        return EditorialImportPackage(
            schema_version=payload.get("schema_version"),
            package_type=PACKAGE_TYPE,
            book=book,
            source=source_ref,
            source_intake_item_id=source.get("item_id") if isinstance(source.get("item_id"), int) else None,
            editions=tuple(editions),
            status=str(payload.get("status") or "DRAFT"),
            incremental=payload.get("incremental_import") if isinstance(payload.get("incremental_import"), dict) else {},
            raw=payload,
        )


def _adapt_canonical(payload: dict[str, Any]) -> EditorialImportPackage:
    unexpected = sorted(set(payload) - CANONICAL_TOP_LEVEL)
    if unexpected:
        raise EditorialPackageValidationError(["Campos não permitidos no pacote: " + ", ".join(unexpected)])
    book = payload.get("book") if isinstance(payload.get("book"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    unknown_book = sorted(set(book) - CANONICAL_BOOK_ALLOWED)
    unknown_source = sorted(set(source) - CANONICAL_SOURCE_ALLOWED)
    if unknown_book or unknown_source:
        errors = []
        if unknown_book:
            errors.append("Campos não permitidos em book: " + ", ".join(unknown_book))
        if unknown_source:
            errors.append("Campos não permitidos em source: " + ", ".join(unknown_source))
        raise EditorialPackageValidationError(errors)
    editions_raw = payload.get("editions") if isinstance(payload.get("editions"), list) else []
    editions: list[EditorialEditionPackage] = []
    for index, raw in enumerate(editions_raw):
        if not isinstance(raw, dict):
            raise EditorialPackageValidationError([f"editions[{index}] deve ser um objeto."])
        allowed = {"language", "locale", "metadata", "frontmatter", "body", "validation", "artifacts"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise EditorialPackageValidationError([f"editions[{index}] contém campos não permitidos: {', '.join(unknown)}"])
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        frontmatter = raw.get("frontmatter") if isinstance(raw.get("frontmatter"), dict) else {}
        body_payload = raw.get("body") if isinstance(raw.get("body"), dict) else {}
        unknown_metadata = sorted(set(metadata) - METADATA_FIELDS)
        unknown_frontmatter = sorted(set(frontmatter) - FRONTMATTER_FIELDS)
        unknown_body = sorted(set(body_payload) - {"status", "stage", "format", "path", "size", "sha256"})
        if unknown_metadata or unknown_frontmatter or unknown_body:
            errors = []
            if unknown_metadata:
                errors.append(f"editions[{index}].metadata contém campos não permitidos: {', '.join(unknown_metadata)}")
            if unknown_frontmatter:
                errors.append(f"editions[{index}].frontmatter contém campos não permitidos: {', '.join(unknown_frontmatter)}")
            if unknown_body:
                errors.append(f"editions[{index}].body contém campos não permitidos: {', '.join(unknown_body)}")
            raise EditorialPackageValidationError(errors)
        editions.append(
            EditorialEditionPackage(
                language=str(raw.get("language") or ""),
                locale=str(raw.get("locale") or ""),
                metadata=metadata,
                frontmatter=frontmatter,
                body=_artifact(body_payload),
                validation=raw.get("validation") if isinstance(raw.get("validation"), dict) else {},
            )
        )
    return EditorialImportPackage(
        schema_version=payload.get("schema_version"),
        package_type=str(payload.get("package_type") or ""),
        book=book,
        source=ArtifactReference(
            path=str(source.get("path") or ""),
            sha256=str(source.get("sha256") or ""),
            size=source.get("size") if isinstance(source.get("size"), int) else None,
            status=str(source.get("status") or ""),
        ),
        source_intake_item_id=source.get("intake_item_id") if isinstance(source.get("intake_item_id"), int) else None,
        editions=tuple(editions),
        status=str(payload.get("status") or "DRAFT"),
        incremental=payload.get("incremental") if isinstance(payload.get("incremental"), dict) else {},
        raw=payload,
    )


def _validate_contract(package: EditorialImportPackage) -> None:
    errors: list[str] = []
    if package.schema_version != 1:
        errors.append("schema_version deve ser 1.")
    if package.package_type != PACKAGE_TYPE:
        errors.append(f"package_type deve ser {PACKAGE_TYPE}.")
    if not BOOK_CODE_RE.fullmatch(str(package.book.get("book_code") or "")):
        errors.append("book.book_code deve seguir o padrão book_0000.")
    for field_name in ("title", "author_name", "source_language"):
        if not str(package.book.get(field_name) or "").strip():
            errors.append(f"book.{field_name} é obrigatório.")
    if not package.editions:
        errors.append("O pacote deve declarar ao menos uma edição.")
    identities: set[tuple[str, str]] = set()
    for index, edition in enumerate(package.editions):
        if not pipeline_language(edition.language, edition.locale):
            errors.append(f"editions[{index}] possui idioma não suportado: {edition.locale or edition.language}.")
        seal = str(edition.metadata.get("seal_name") or edition.metadata.get("imprint_name") or "").strip()
        identity = (edition.locale, seal.casefold())
        if identity in identities:
            errors.append(f"Edição duplicada para idioma/selo: {edition.locale}/{seal}.")
        identities.add(identity)
        if not str(edition.metadata.get("title") or "").strip():
            errors.append(f"editions[{index}].metadata.title é obrigatório.")
        if edition.body and edition.body.status not in {"ABSENT", "DRAFT", "OFFICIAL"}:
            errors.append(f"editions[{index}].body.status inválido.")
        for flag, text in (("has_preface", "preface_text"), ("has_introduction", "introduction_text"), ("has_epilogue", "epilogue_text")):
            if edition.frontmatter.get(flag) and not str(edition.frontmatter.get(text) or "").strip():
                errors.append(f"editions[{index}].frontmatter.{text} não pode ser vazio quando habilitado.")
        if edition.body and edition.body.status == "OFFICIAL" and edition.validation.get("status") != "PASS":
            errors.append(f"editions[{index}] não pode promover body OFFICIAL sem validação PASS.")
    if errors:
        raise EditorialPackageValidationError(errors)


def _safe_artifact_path(root: Path, declared_path: str) -> Path:
    pure = PurePosixPath(declared_path.replace("\\", "/"))
    if not declared_path or pure.is_absolute() or ".." in pure.parts:
        raise EditorialPackageValidationError([f"Caminho de artefato inseguro: {declared_path!r}"])
    candidates = [root.joinpath(*pure.parts)]
    if len(pure.parts) > 1:
        candidates.append(root / pure.name)
    for candidate in candidates:
        if candidate.is_symlink():
            raise EditorialPackageValidationError([f"Artefato não pode ser symlink: {declared_path}"])
        if candidate.is_file():
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise EditorialPackageValidationError([f"Artefato fora da raiz permitida: {declared_path}"]) from exc
            return resolved
    raise EditorialPackageValidationError([f"Artefato declarado não encontrado: {declared_path}"])


def _validate_artifact(root: Path, reference: ArtifactReference, label: str) -> Path:
    if not SHA256_RE.fullmatch(reference.sha256):
        raise EditorialPackageValidationError([f"{label}.sha256 inválido."])
    path = _safe_artifact_path(root, reference.path)
    data = path.read_bytes()
    if reference.size is not None and len(data) != reference.size:
        raise EditorialPackageValidationError([f"{label}.size divergente: declarado={reference.size}, real={len(data)}."])
    digest = sha256_bytes(data)
    if digest != reference.sha256:
        raise EditorialPackageValidationError([f"{label}.sha256 divergente: declarado={reference.sha256}, real={digest}."])
    return path


def load_and_validate_package(package_path: str | Path, *, artifact_root: str | Path | None = None) -> ValidatedEditorialPackage:
    path = Path(package_path).expanduser().resolve()
    payload, package_bytes = _read_json(path)
    package = _adapt_canonical(payload) if "package_type" in payload else LegacyEditorialPackageV1Adapter.adapt(payload)
    _validate_contract(package)
    root = Path(artifact_root).expanduser().resolve() if artifact_root else path.parent.resolve()
    artifact_paths = {"source": _validate_artifact(root, package.source, "source")}
    warnings: list[str] = []
    for index, edition in enumerate(package.editions):
        if edition.body:
            artifact_paths[f"body:{edition.locale}"] = _validate_artifact(root, edition.body, f"editions[{index}].body")
            if edition.body.status not in {"OFFICIAL", "ABSENT"}:
                warnings.append(f"{edition.locale}: corpo será registrado para revisão, sem publicação ou build.")
    return ValidatedEditorialPackage(
        package=package,
        package_path=path,
        package_sha256=sha256_bytes(package_bytes),
        artifact_root=root,
        artifact_paths=artifact_paths,
        warnings=tuple(warnings),
    )
