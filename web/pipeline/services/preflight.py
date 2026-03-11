from __future__ import annotations

import json
import os
import re
from pathlib import Path

from gaiden.openai_client import get_client

from . import paths

SEGMENT_MAX_CHARS = 18000
SEGMENT_MIN_CHARS = 5000
DEFAULT_MODEL = os.environ.get("GAIDEN_PREFLIGHT_MODEL", "gpt-5-mini")


def _pick_source_text(edition) -> Path:
    book_code = edition.work.code
    translated_merge = paths.data_dir() / "translated" / book_code / "merge_refine_clean.txt"
    candidates = [
        translated_merge,
        paths.merge_refine_path(edition),
        paths.merge_polish_path(edition),
        paths.merge_translate_path(edition),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No canonical merge text found for pre-flight: book_code={book_code}"
    )


def _detect_heading(paragraph: str) -> str | None:
    text = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
    if not text:
        return None
    patterns = [
        r"^(?:#+\s*)?chapter\s+[0-9ivxlc]+\b.*$",
        r"^(?:#+\s*)?the adventure of .+$",
        r"^(?:#+\s*)?adventure\s+[0-9ivxlc]+\b.*$",
        r"^(?:#+\s*)?[0-9ivxlc]+\.\s+.+$",
    ]
    for pattern in patterns:
        if re.match(pattern, text, flags=re.IGNORECASE):
            return text
    return None


def _segment_text(text: str) -> list[dict[str, str]]:
    paragraphs = text.split("\n\n")
    segments: list[dict[str, str]] = []
    current_label = "Opening"
    current_parts: list[str] = []
    current_chars = 0

    for paragraph in paragraphs:
        heading = _detect_heading(paragraph)
        para_chars = len(paragraph) + 2
        should_cut = bool(
            current_parts
            and (
                (heading and current_chars >= SEGMENT_MIN_CHARS)
                or current_chars + para_chars > SEGMENT_MAX_CHARS
            )
        )
        if should_cut:
            segments.append(
                {
                    "label": current_label,
                    "text": "\n\n".join(current_parts).strip(),
                }
            )
            current_parts = []
            current_chars = 0
            if heading:
                current_label = heading
        elif heading and not current_parts:
            current_label = heading

        current_parts.append(paragraph)
        current_chars += para_chars

    if current_parts:
        segments.append(
            {
                "label": current_label,
                "text": "\n\n".join(current_parts).strip(),
            }
        )

    return [segment for segment in segments if segment["text"]]


def _extract_output_text(response) -> str:
    try:
        text = getattr(response, "output_text", "") or ""
        if text.strip():
            return text.strip()
    except Exception:
        pass

    try:
        output = getattr(response, "output", None) or []
        for item in output:
            content = getattr(item, "content", None) or []
            for part in content:
                text = getattr(part, "text", "") or ""
                if text.strip():
                    return text.strip()
    except Exception:
        pass

    return ""


def _json_prompt(segment_label: str, segment_text: str) -> list[dict[str, object]]:
    system_prompt = (
        "You are a senior editorial pre-flight reviewer for commercial literary fiction. "
        "Analyze only real textual issues before markdown structuring/build. "
        "Ignore missing markdown headings, figures, image placement, and visual chapter separation. "
        "Be technical, direct, and editorial."
    )
    user_prompt = (
        "Analyze the segment below for editorial readiness before MD/KDP build.\n"
        "Return valid JSON only with this schema:\n"
        "{\n"
        '  "critical": ["..."],\n'
        '  "medium": ["..."],\n'
        '  "light": ["..."],\n'
        '  "good": ["..."]\n'
        "}\n"
        "Rules:\n"
        "- Do not rewrite the text.\n"
        "- Do not summarize the story.\n"
        "- Each item must be a short factual bullet.\n"
        "- Mention concrete local evidence when relevant.\n"
        "- Report only issues that affect content, continuity, consistency, or fluency.\n"
        "- If a bucket has no findings, return an empty array.\n\n"
        f"Segment label: {segment_label}\n\n"
        "Text:\n"
        f"{segment_text}"
    )
    return [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": system_prompt}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_prompt}],
        },
    ]


def _normalize_items(segment_label: str, payload: dict[str, object], key: str) -> list[str]:
    raw_items = payload.get(key) or []
    if not isinstance(raw_items, list):
        return []
    items: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text:
            continue
        if text.startswith(f"[{segment_label}] "):
            items.append(text)
            continue
        items.append(f"[{segment_label}] {text}")
    return items


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _fallback_issue(segment_label: str, raw_output: str) -> str:
    snippet = " ".join(raw_output.split())
    if len(snippet) > 220:
        snippet = snippet[:217] + "..."
    return f"[{segment_label}] Pre-flight returned non-JSON output; review manually. Raw: {snippet}"


def _build_markdown_report(source_path: Path, analysis: dict[str, object]) -> str:
    lines = [
        "# Pre-flight Editorial Report",
        "",
        f"- Source: `{source_path}`",
        f"- Segments analyzed: `{analysis['segment_count']}`",
        f"- Model: `{analysis['model']}`",
        "",
        "## 1. PROBLEMAS CRITICOS",
        "",
    ]

    critical = analysis["critical"]
    if critical:
        lines.extend(f"- {item}" for item in critical)
    else:
        lines.append("- Nenhum problema critico detectado.")

    lines.extend(["", "## 2. PROBLEMAS MEDIOS", ""])
    medium = analysis["medium"]
    if medium:
        lines.extend(f"- {item}" for item in medium)
    else:
        lines.append("- Nenhum problema medio detectado.")

    lines.extend(["", "## 3. PROBLEMAS LEVES", ""])
    light = analysis["light"]
    if light:
        lines.extend(f"- {item}" for item in light)
    else:
        lines.append("- Nenhum problema leve relevante detectado.")

    lines.extend(["", "## 4. O QUE ESTA BOM", ""])
    good = analysis["good"]
    if good:
        lines.extend(f"- {item}" for item in good)
    else:
        lines.append("- Nenhum destaque positivo registrado.")

    lines.extend(
        [
            "",
            "## 5. VEREDITO FINAL",
            "",
            f"- Classificacao: **{analysis['verdict']}**",
            f"- Leitura: {analysis['verdict_reason']}",
            "",
            "- Marcacao do problema: "
            + (
                "problemas reais de texto que precisam ser corrigidos antes do build final"
                if analysis["verdict"] == "precisa de correcoes antes do MD"
                else "apenas ajustes textuais localizados; a estrutura visual/MD pode seguir depois"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _decide_verdict(critical: list[str], medium: list[str]) -> tuple[str, str]:
    if critical:
        return (
            "precisa de correcoes antes do MD",
            "Ha problemas que podem quebrar sentido, continuidade ou integridade textual.",
        )
    if medium:
        return (
            "pronto para MD com pequenos ajustes",
            "O texto esta semanticamente utilizavel, mas ainda ha pontos de fluidez e consistencia a ajustar.",
        )
    return (
        "pronto para MD",
        "Nao ha sinais materiais de quebra textual; os proximos passos podem ser estruturais.",
    )


def run_preflight(edition) -> dict[str, object]:
    source_path = _pick_source_text(edition)
    text = source_path.read_text(encoding="utf-8")
    segments = _segment_text(text)
    if not segments:
        raise ValueError(f"Pre-flight source is empty: {source_path}")

    client = get_client()
    critical: list[str] = []
    medium: list[str] = []
    light: list[str] = []
    good: list[str] = []
    raw_segments: list[dict[str, object]] = []

    for idx, segment in enumerate(segments, start=1):
        label = segment["label"] or f"Segment {idx}"
        response = client.responses.create(
            model=DEFAULT_MODEL,
            input=_json_prompt(label, segment["text"]),
            max_output_tokens=1800,
        )
        raw_output = _extract_output_text(response)
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError:
            payload = {"critical": [_fallback_issue(label, raw_output)], "medium": [], "light": [], "good": []}

        critical.extend(_normalize_items(label, payload, "critical"))
        medium.extend(_normalize_items(label, payload, "medium"))
        light.extend(_normalize_items(label, payload, "light"))
        good.extend(_normalize_items(label, payload, "good"))
        raw_segments.append(
            {
                "label": label,
                "chars": len(segment["text"]),
                "raw_output": raw_output,
            }
        )

    critical = _dedupe(critical)
    medium = _dedupe(medium)
    light = _dedupe(light)
    good = _dedupe(good)
    verdict, verdict_reason = _decide_verdict(critical, medium)

    json_path = paths.preflight_json_path(edition)
    md_path = paths.preflight_md_path(edition)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    analysis = {
        "schema": "preflight_v1",
        "model": DEFAULT_MODEL,
        "source_path": str(source_path),
        "segment_count": len(segments),
        "critical": critical,
        "medium": medium,
        "light": light,
        "good": good,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "segments": raw_segments,
    }
    json_path.write_text(json.dumps(analysis, ensure_ascii=True, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown_report(source_path, analysis), encoding="utf-8")

    return {
        "source_path": str(source_path),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "verdict": verdict,
        "segment_count": len(segments),
        "critical_count": len(critical),
        "medium_count": len(medium),
        "light_count": len(light),
    }
