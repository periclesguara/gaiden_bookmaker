from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from gaiden.openai_client import get_client

from . import edition_meta, paths, utils


CAP_CHUNK_RE = re.compile(r"^cap_(\d+)_chunk(\d+)\.txt$", re.IGNORECASE)
CHUNK_CAP_RE = re.compile(r"^chunk(\d+)_cap_(\d+)\.txt$", re.IGNORECASE)


TRANSLATE_CONTRACTS = {
    "en": "gaiden/contracts/en_modern_2025.json",
    "es": "gaiden/contracts/en_es_2025.json",
    "ptbr": "gaiden/contracts/en_ptbr_2025.json",
    "de": "gaiden/contracts/en_de_krimi_2025.json",
}

REFINE_CONTRACTS = {
    "en": "gaiden/contracts/refine/en_refine_2025.json",
    "es": "gaiden/contracts/refine/es_refine_2025.json",
    "ptbr": "gaiden/contracts/refine/ptbr_refine_2025.json",
}

POLISH_CONTRACTS = {
    "en": "gaiden/contracts/polish/en_polish_2025.json",
}


@dataclass
class PipelineResult:
    translated_path: Path
    refined_path: Path
    polished_path: Path | None
    canonical_path: Path
    polished: bool


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    digits = "".join(ch for ch in book_code if ch.isdigit())
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Contract not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_messages(system_prompt: str, user_prompt: str, text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    user_text = (user_prompt or "{text}").replace("{text}", text)
    messages.append({"role": "user", "content": user_text})
    return messages


def _run_agent(
    system_prompt: str,
    user_prompt: str,
    text: str,
    model: str,
    temperature: float,
    max_output_tokens: int,
) -> str:
    client = get_client()
    messages = _build_messages(system_prompt, user_prompt, text)
    resp = client.responses.create(
        model=model,
        input=messages,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    try:
        output = getattr(resp, "output_text", "").strip()
    except Exception:
        output = ""
    if not output:
        try:
            output = resp.output[0].content[0].text.strip()
        except Exception:
            output = ""
    return output


def _refine_contract_to_prompts(payload: dict) -> tuple[str, str, str]:
    model = payload.get("model", "gpt-5.1")
    instructions = payload.get("instructions", {}) or {}
    goal = instructions.get("goal", "")
    rules = instructions.get("rules", []) or []
    style = instructions.get("style", {}) or {}
    output = instructions.get("output", {}) or {}

    lines = []
    if goal:
        lines.append(f"Goal: {goal}")
    if rules:
        lines.append("Rules:")
        for rule in rules:
            lines.append(f"- {rule}")
    if style:
        lines.append("Style:")
        for key, value in style.items():
            lines.append(f"- {key}: {value}")
    if output:
        lines.append("Output:")
        for key, value in output.items():
            lines.append(f"- {key}: {value}")

    system_prompt = "\n".join(lines).strip()
    user_prompt = "Refine the following text. Return only the refined text:\n\n{text}"
    return system_prompt, user_prompt, model


def _translate_contract_to_prompts(payload: dict) -> tuple[str, str, str, float, int]:
    model = payload.get("model", "gpt-5.1")
    temperature = float(payload.get("temperature", 0.4))
    max_output_tokens = int(payload.get("max_output_tokens", 2000))
    system_prompt = payload.get("system_prompt") or payload.get("system") or ""
    user_prompt = payload.get("user_prompt") or payload.get("user") or "{text}"
    return system_prompt, user_prompt, model, temperature, max_output_tokens


def _polish_contract_to_prompts(payload: dict) -> tuple[str, str, str]:
    model = payload.get("model", "gpt-5.1")
    system_prompt = payload.get("system_prompt") or payload.get("system") or ""
    user_prompt = payload.get("user_prompt") or payload.get("user") or "{text}"
    return system_prompt, user_prompt, model


def _group_chapter_blocks(split_dir: Path) -> dict[int, list[Path]]:
    groups: dict[int, list[tuple[int, Path]]] = {}
    for path in sorted(split_dir.glob("*.txt")):
        name = path.name
        m = CAP_CHUNK_RE.match(name)
        if m:
            chapter = int(m.group(1))
            block = int(m.group(2))
        else:
            m = CHUNK_CAP_RE.match(name)
            if not m:
                continue
            block = int(m.group(1))
            chapter = int(m.group(2))
        groups.setdefault(chapter, []).append((block, path))

    ordered: dict[int, list[Path]] = {}
    for chapter in sorted(groups.keys()):
        ordered[chapter] = [p for _, p in sorted(groups[chapter], key=lambda item: item[0])]
    return ordered


def _join_blocks(blocks: list[str]) -> str:
    clean = [b.strip() for b in blocks if b.strip()]
    if not clean:
        return ""
    return "\n\n".join(clean).strip() + "\n"


def _should_polish(refined_text: str, polish_available: bool) -> bool:
    if not polish_available:
        return False
    if os.environ.get("GAIDEN_FORCE_POLISH", "").strip() == "1":
        return True
    if os.environ.get("GAIDEN_SKIP_POLISH", "").strip() == "1":
        return False

    sentences = re.split(r"(?<=[.!?])\s+", refined_text.strip())
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if not lengths:
        return False
    avg_len = sum(lengths) / len(lengths)
    if avg_len > 30 or max(lengths) > 60:
        return True
    if "  " in refined_text or "\t" in refined_text:
        return True
    return False


def run_new_mode_pipeline(edition, target_language: str) -> PipelineResult:
    lang = utils.normalize_lang(target_language)
    if lang not in TRANSLATE_CONTRACTS:
        raise ValueError(f"No translate contract for language={lang}")
    book_code = edition_meta.book_code(edition)
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001.")

    split_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "split_01_by_chapter"
    if not split_dir.exists():
        raise FileNotFoundError("Split by Chapter not found.")

    translate_contract_path = _project_root() / TRANSLATE_CONTRACTS[lang]
    translate_payload = _load_json(translate_contract_path)
    t_system, t_user, t_model, t_temp, t_max = _translate_contract_to_prompts(translate_payload)

    refine_contract_path = _project_root() / REFINE_CONTRACTS.get(lang, "")
    refine_payload = _load_json(refine_contract_path) if refine_contract_path.is_file() else {}
    if refine_payload:
        r_system, r_user, r_model = _refine_contract_to_prompts(refine_payload)
    elif lang == "de":
        from . import refine_de

        r_system = refine_de.KAISER_SYSTEM_PROMPT
        r_user = "Refine the following text. Return only the refined text:\n\n{text}"
        r_model = refine_de.DEFAULT_MODEL
    else:
        r_system = (
            f"Refine the text in {lang}. Keep meaning, tone, and structure. "
            "Do not summarize. Do not add commentary."
        )
        r_user = "Refine the following text. Return only the refined text:\n\n{text}"
        r_model = t_model

    polish_payload = None
    polish_contract_path = _project_root() / POLISH_CONTRACTS.get(lang, "")
    if polish_contract_path.is_file():
        polish_payload = _load_json(polish_contract_path)
        system_prompt = polish_payload.get("system_prompt") or polish_payload.get("system") or ""
        if "@@P" in system_prompt and os.environ.get("GAIDEN_ALLOW_MARKER_POLISH", "").strip() != "1":
            polish_payload = None
    elif lang == "de":
        from . import refine_de

        polish_payload = {"system_prompt": refine_de.BISMARCK_SYSTEM_PROMPT, "model": refine_de.DEFAULT_MODEL}

    p_system, p_user, p_model = _polish_contract_to_prompts(polish_payload) if polish_payload else ("", "", t_model)

    build_dir = paths.edition_build_dir_for_language(book_code, lang)
    chapters_dir = build_dir / "chapters"
    translate_dir = chapters_dir / "translate"
    refine_dir = chapters_dir / "refine"
    polish_dir = chapters_dir / "polish"
    translate_dir.mkdir(parents=True, exist_ok=True)
    refine_dir.mkdir(parents=True, exist_ok=True)
    polish_dir.mkdir(parents=True, exist_ok=True)

    chapter_blocks = _group_chapter_blocks(split_dir)
    if not chapter_blocks:
        raise ValueError("No chapter blocks found in split_01_by_chapter.")

    translated_chapters: list[str] = []
    refined_chapters: list[str] = []
    refined_blocks_map: dict[int, list[str]] = {}
    polished_chapters: list[str] = []

    for chapter_num, block_paths in chapter_blocks.items():
        translated_blocks: list[str] = []
        for block_path in block_paths:
            text = block_path.read_text(encoding="utf-8")
            translated = _run_agent(t_system, t_user, text, t_model, t_temp, t_max)
            translated_blocks.append(translated)

        translated_chapter = _join_blocks(translated_blocks)
        translated_path = translate_dir / f"translated_ch{chapter_num:02d}.txt"
        translated_path.write_text(translated_chapter, encoding="utf-8")
        translated_chapters.append(translated_chapter)

        refined_blocks: list[str] = []
        for block_text in translated_blocks:
            refined = _run_agent(r_system, r_user, block_text, r_model, 0.4, 2000)
            refined_blocks.append(refined)
        refined_blocks_map[chapter_num] = refined_blocks

        refined_chapter = _join_blocks(refined_blocks)
        refined_path = refine_dir / f"refined_ch{chapter_num:02d}.txt"
        refined_path.write_text(refined_chapter, encoding="utf-8")
        refined_chapters.append(refined_chapter)

    translated_merge = _join_blocks(translated_chapters)
    refined_merge = _join_blocks(refined_chapters)

    merge_translate_path = build_dir / "merge_translate.txt"
    merge_translate_path.write_text(translated_merge, encoding="utf-8")

    refined_stage_path = build_dir / "merge_refine_stage.txt"
    refined_stage_path.write_text(refined_merge, encoding="utf-8")

    polished_path = None
    polished_used = _should_polish(refined_merge, polish_payload is not None)
    if polished_used:
        for chapter_num in sorted(refined_blocks_map.keys()):
            polished_blocks: list[str] = []
            for block_text in refined_blocks_map[chapter_num]:
                polished = _run_agent(p_system, p_user, block_text, p_model, 0.4, 2000)
                polished_blocks.append(polished)
            polished_chapter = _join_blocks(polished_blocks)
            polished_path_ch = polish_dir / f"polished_ch{chapter_num:02d}.txt"
            polished_path_ch.write_text(polished_chapter, encoding="utf-8")
            polished_chapters.append(polished_chapter)
        polished_merge = _join_blocks(polished_chapters)
        polished_path = build_dir / "merge_polish.txt"
        polished_path.write_text(polished_merge, encoding="utf-8")
        canonical_source = polished_path
    else:
        canonical_source = refined_stage_path

    canonical_path = build_dir / "merge_refine.txt"
    canonical_path.write_text(canonical_source.read_text(encoding="utf-8"), encoding="utf-8")

    return PipelineResult(
        translated_path=merge_translate_path,
        refined_path=refined_stage_path,
        polished_path=polished_path,
        canonical_path=canonical_path,
        polished=polished_used,
    )
