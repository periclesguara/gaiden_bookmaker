from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol


CONTRACT_VERSION = "gaiden_normalize_decision_v2"
NORMALIZER_VERSION = "gaiden_block_normalizer_v2"

KEEP_DECISIONS = frozenset(
    {
        "KEEP_BODY",
        "KEEP_HEADING",
        "KEEP_AUTHORIAL_FRONT",
        "KEEP_AUTHORIAL_BACK",
    }
)
DROP_DECISIONS = frozenset(
    {
        "DROP_PLATFORM_CONTRACT",
        "DROP_PLATFORM_LICENSE",
        "DROP_DIGITIZATION_CREDIT",
        "DROP_PLATFORM_METADATA",
        "DROP_EXTERNAL_COLOPHON",
        "DROP_DUPLICATED_TOC",
    }
)
DECISIONS = KEEP_DECISIONS | DROP_DECISIONS | {"REVIEW_REQUIRED"}
SOURCE_FAMILIES = frozenset(
    {"project_gutenberg", "internet_archive", "standard_ebooks", "other", "none"}
)
HEADING_TYPES = frozenset(
    {
        "title",
        "part",
        "chapter",
        "subchapter",
        "preface",
        "introduction",
        "epilogue",
        "appendix",
        "other",
    }
)
_ALLOWED_TOP = frozenset({"schema", "source_sha256", "blocks"})
_ALLOWED_BLOCK = frozenset(
    {
        "block_id",
        "start_offset",
        "end_offset",
        "decision",
        "source_family",
        "confidence",
        "evidence",
        "heading_level",
        "heading_type",
        "heading_text",
    }
)
_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+)?(?:chapter|book|part|section)\s+(?:\d+|[IVXLCDM]+|[A-Z]+)\b.*$|"
    r"^(?:#{1,6}\s+)?(?:preface|foreword|introduction|prologue|epilogue|afterword|appendix)\b.*$",
    re.IGNORECASE,
)


class NormalizeContractError(ValueError):
    """The model response cannot safely govern source transformation."""


class BlockClassifier(Protocol):
    model: str

    def classify(self, *, source_sha256: str, blocks: list[dict[str, object]]) -> dict[str, object]: ...


@dataclass(frozen=True)
class SourceBlock:
    block_id: str
    start_offset: int
    end_offset: int
    text: str

    def packet(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "text": self.text,
        }


@dataclass(frozen=True)
class NormalizeResult:
    normalized_body: str
    manifest: dict[str, object]
    structure_map: dict[str, object]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _segment(text: str) -> list[SourceBlock]:
    """Partition text into exact, contiguous paragraph-like blocks."""
    if not text:
        raise NormalizeContractError("O texto extraído está vazio.")
    boundaries = [0]
    boundaries.extend(match.end() for match in re.finditer(r"\n[ \t]*\n+", text))
    if boundaries[-1] != len(text):
        boundaries.append(len(text))
    ranges: list[tuple[int, int]] = []
    max_block_characters = 12_000
    for start, end in zip(boundaries, boundaries[1:]):
        while end - start > max_block_characters:
            preferred = text.rfind("\n", start, start + max_block_characters + 1)
            cut = preferred + 1 if preferred >= start else start + max_block_characters
            ranges.append((start, cut))
            start = cut
        if end > start:
            ranges.append((start, end))
    blocks = [
        SourceBlock(f"block_{index:04d}", start, end, text[start:end])
        for index, (start, end) in enumerate(ranges, start=1)
    ]
    if not blocks or blocks[0].start_offset != 0 or blocks[-1].end_offset != len(text):
        raise NormalizeContractError("A segmentação não cobre integralmente o texto extraído.")
    if any(left.end_offset != right.start_offset for left, right in zip(blocks, blocks[1:])):
        raise NormalizeContractError("A segmentação contém lacuna ou sobreposição.")
    return blocks


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormalizeContractError(f"{field} deve ser numérico.")
    return float(value)


def _validate_decision(source: SourceBlock, raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise NormalizeContractError("Cada decisão deve ser um objeto JSON.")
    unknown = set(raw) - _ALLOWED_BLOCK
    if unknown:
        raise NormalizeContractError(f"Campos desconhecidos na decisão: {sorted(unknown)}")
    required = {
        "block_id",
        "start_offset",
        "end_offset",
        "decision",
        "source_family",
        "confidence",
        "evidence",
    }
    missing = required - set(raw)
    if missing:
        raise NormalizeContractError(f"Campos ausentes na decisão: {sorted(missing)}")
    if raw["block_id"] != source.block_id:
        raise NormalizeContractError("block_id divergente do bloco enviado.")
    if raw["start_offset"] != source.start_offset or raw["end_offset"] != source.end_offset:
        raise NormalizeContractError(f"Offsets inválidos em {source.block_id}.")
    decision = raw["decision"]
    if decision not in DECISIONS:
        raise NormalizeContractError(f"Decisão inválida em {source.block_id}.")
    if raw["source_family"] not in SOURCE_FAMILIES:
        raise NormalizeContractError(f"source_family inválida em {source.block_id}.")
    confidence = _number(raw["confidence"], "confidence")
    if not 0 <= confidence <= 1:
        raise NormalizeContractError(f"confidence fora do intervalo em {source.block_id}.")
    evidence = raw["evidence"]
    if not isinstance(evidence, str) or not evidence.strip():
        raise NormalizeContractError(f"Decisão sem evidência em {source.block_id}.")
    normalized = dict(raw)
    normalized["confidence"] = confidence
    if decision == "KEEP_HEADING":
        heading_required = {"heading_level", "heading_type", "heading_text"}
        if not heading_required.issubset(raw):
            raise NormalizeContractError(f"Heading incompleto em {source.block_id}.")
        level = raw["heading_level"]
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 6:
            raise NormalizeContractError(f"heading_level inválido em {source.block_id}.")
        if raw["heading_type"] not in HEADING_TYPES:
            raise NormalizeContractError(f"heading_type inválido em {source.block_id}.")
        heading_text = raw["heading_text"]
        if not isinstance(heading_text, str) or not heading_text.strip():
            raise NormalizeContractError(f"heading_text inválido em {source.block_id}.")
        if heading_text.strip() not in source.text:
            raise NormalizeContractError(f"Heading inventado em {source.block_id}.")
    elif any(key in raw for key in ("heading_level", "heading_type", "heading_text")):
        raise NormalizeContractError(f"Campos de heading são proibidos em {source.block_id}.")
    return normalized


def _validate_payload(
    payload: object,
    *,
    source_sha256: str,
    source_blocks: list[SourceBlock],
) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise NormalizeContractError("A resposta Qwen deve ser um objeto JSON.")
    unknown = set(payload) - _ALLOWED_TOP
    if unknown:
        raise NormalizeContractError(f"Campos desconhecidos no contrato: {sorted(unknown)}")
    if payload.get("schema") != CONTRACT_VERSION:
        raise NormalizeContractError("Versão de contrato Qwen inválida.")
    if payload.get("source_sha256") != source_sha256:
        raise NormalizeContractError("source_sha256 divergente no contrato Qwen.")
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != len(source_blocks):
        raise NormalizeContractError("O contrato deve decidir cada bloco exatamente uma vez.")
    return [_validate_decision(source, raw) for source, raw in zip(source_blocks, raw_blocks)]


def _structure_type(text: str) -> tuple[str, int] | None:
    stripped = re.sub(r"^#{1,6}\s+", "", text.strip()).strip()
    if not stripped or not _HEADING_RE.fullmatch(stripped):
        return None
    lower = stripped.casefold()
    if lower.startswith(("preface", "foreword")):
        return "preface", 1
    if lower.startswith(("introduction", "prologue")):
        return "introduction", 1
    if lower.startswith(("epilogue", "afterword")):
        return "epilogue", 1
    if lower.startswith("appendix"):
        return "appendix", 1
    if lower.startswith("chapter"):
        return "chapter", 1
    if lower.startswith(("part", "book")):
        return "part", 1
    return "subchapter", 2


def normalize_extracted_text(
    extracted_text: str,
    *,
    raw_sha256: str,
    classifier: BlockClassifier,
) -> NormalizeResult:
    blocks = _segment(extracted_text)
    payload = classifier.classify(
        source_sha256=raw_sha256,
        blocks=[block.packet() for block in blocks],
    )
    decisions = _validate_payload(payload, source_sha256=raw_sha256, source_blocks=blocks)
    kept_parts: list[str] = []
    structure: list[dict[str, object]] = []
    normalized_offset = 0
    warnings: list[str] = []
    for source, decision in zip(blocks, decisions):
        selected = decision["decision"] in KEEP_DECISIONS
        if decision["decision"] == "REVIEW_REQUIRED":
            warnings.append(f"{source.block_id}: {decision['evidence']}")
        normalized_start = normalized_offset if selected else None
        if selected:
            kept_parts.append(source.text)
            normalized_offset += len(source.text)
            heading = None
            if decision["decision"] == "KEEP_HEADING":
                heading = (
                    str(decision["heading_type"]),
                    int(decision["heading_level"]),
                    str(decision["heading_text"]).strip(),
                )
            else:
                detected = _structure_type(source.text)
                if detected:
                    heading = (detected[0], detected[1], source.text.strip())
            if heading:
                local = source.text.find(heading[2])
                structure.append(
                    {
                        "sequence": len(structure) + 1,
                        "block_id": source.block_id,
                        "type": heading[0],
                        "level": heading[1],
                        "heading_original": heading[2],
                        "start_offset": int(normalized_start) + max(0, local),
                        "end_offset": int(normalized_start) + max(0, local) + len(heading[2]),
                        "confidence": decision["confidence"],
                        "review_required": False,
                    }
                )
        decision["normalized_start_offset"] = normalized_start
        decision["normalized_end_offset"] = normalized_offset if selected else None
    normalized = "".join(kept_parts)
    if not normalized.strip():
        raise NormalizeContractError("O contrato removeria todo o miolo.")
    normalized_sha = sha256_bytes(normalized.encode("utf-8"))
    review_required = bool(warnings)
    manifest = {
        "schema": "gaiden_normalize_manifest_v2",
        "normalizer_version": NORMALIZER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "qwen_model": getattr(classifier, "model", classifier.__class__.__name__),
        "raw_sha256": raw_sha256,
        "normalized_sha256": normalized_sha,
        "source_character_count": len(extracted_text),
        "normalized_character_count": len(normalized),
        "kept_block_count": sum(row["decision"] in KEEP_DECISIONS for row in decisions),
        "removed_block_count": sum(row["decision"] in DROP_DECISIONS for row in decisions),
        "review_block_count": sum(row["decision"] == "REVIEW_REQUIRED" for row in decisions),
        "review_required": review_required,
        "validated": not review_required,
        "warnings": warnings,
        "blocks": decisions,
    }
    structure_map = {
        "schema": "gaiden_structure_map_v1",
        "normalizer_version": NORMALIZER_VERSION,
        "normalized_sha256": normalized_sha,
        "validated": not review_required,
        "review_required": review_required,
        "structures": structure,
    }
    return NormalizeResult(normalized, manifest, structure_map)


def parse_classifier_json(content: str) -> dict[str, object]:
    """Parse one strict JSON object; Markdown fences and surrounding prose are invalid."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise NormalizeContractError("A resposta Qwen não é JSON estrito.") from exc
    if not isinstance(payload, dict):
        raise NormalizeContractError("A resposta Qwen deve ser um objeto JSON.")
    return payload
