from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gaiden.openai_client import get_client

from . import edition_meta, md_transform, paths, utils

DEFAULT_KAISER_AGENT = "KAISER"
DEFAULT_BISMARCK_AGENT = "BISMARCK"
DEFAULT_MODEL = "gpt-5-chat-latest"

KAISER_SYSTEM_PROMPT = (
    "You are KAISER, a senior German literary editor. "
    "Refine the German text for fluency, clarity, and coherence while preserving "
    "meaning, tone, chronology, and structure. "
    "Do not summarize, add content, remove content, or reorder paragraphs. "
    "Keep paragraph boundaries exactly as in the input. "
    "Output plain text only, no metadata, no notes."
)

BISMARCK_SYSTEM_PROMPT = (
    "You are BISMARCK, a final-pass German editorial polisher. "
    "Polish style, rhythm, grammar, and punctuation without altering meaning or structure. "
    "Do not summarize, add, or remove content. "
    "Keep paragraph boundaries exactly as in the input. "
    "Output plain text only, no metadata, no notes."
)


@dataclass
class RefineDeResult:
    input_path: Path
    output_path: Path
    output_generic_path: Path
    chunks: int
    agent_kaiser: str
    agent_bismarck: str
    model: str
    text: str


def _resolve_input_path(edition) -> Path:
    build_dir = paths.edition_build_dir(edition)
    lang = utils.normalize_lang(edition_meta.language_code(edition))
    candidates = [
        build_dir / f"merge_translate_{lang}_clean.txt",
        build_dir / f"merge_translate_{lang}.txt",
        build_dir / "merge_translate_clean.txt",
        build_dir / "merge_translate.txt",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Nenhum merge_translate encontrado para {lang} em {build_dir}."
    )


def _resolve_output_paths(edition, lang: str) -> tuple[Path, Path]:
    build_dir = paths.edition_build_dir(edition)
    output_lang = build_dir / f"merge_refine_{lang}.txt"
    output_generic = build_dir / "merge_refine.txt"
    return output_lang, output_generic


def _split_by_chapter(text: str) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        if md_transform.CHAPTER_RE.match(line.strip()):
            if current:
                chunks.append("\n".join(current).strip())
                current = []
        current.append(line)

    if current:
        chunks.append("\n".join(current).strip())

    return [chunk for chunk in chunks if chunk.strip()]


def _extract_output_text(resp) -> str:
    try:
        return resp.output[0].content[0].text
    except Exception:
        pass
    try:
        return resp.output_text
    except Exception:
        pass
    raise RuntimeError("Nao foi possivel extrair texto da resposta do modelo.")


def _strip_heading_markers(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("## "):
        lines[0] = lines[0][3:].strip()
    return "\n".join(lines).strip() + "\n"


def _run_agent(client, model: str, system_prompt: str, text: str) -> str:
    clean_text = _strip_heading_markers(text)
    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": clean_text}],
            }
        ],
    )
    return _extract_output_text(resp).strip()

def refine_text_kaiser_bismarck(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    kaiser_prompt: str = KAISER_SYSTEM_PROMPT,
    bismarck_prompt: str = BISMARCK_SYSTEM_PROMPT,
    agent_kaiser: str = DEFAULT_KAISER_AGENT,
    agent_bismarck: str = DEFAULT_BISMARCK_AGENT,
) -> str:
    client = get_client()
    refined = _run_agent(client, model, kaiser_prompt, text)
    polished = _run_agent(client, model, bismarck_prompt, refined)
    return polished.strip()


def run_refine_de_kaiser_bismarck(
    edition,
    *,
    input_path: Path | None = None,
    model: str = DEFAULT_MODEL,
    kaiser_prompt: str = KAISER_SYSTEM_PROMPT,
    bismarck_prompt: str = BISMARCK_SYSTEM_PROMPT,
    agent_kaiser: str = DEFAULT_KAISER_AGENT,
    agent_bismarck: str = DEFAULT_BISMARCK_AGENT,
    output_path: Path | None = None,
    write_output: bool | None = None,
) -> RefineDeResult:
    lang = utils.normalize_lang(edition_meta.language_code(edition))
    if lang != "de":
        raise ValueError("Refine DE so suporta edicoes em alemao (de).")

    source_path = input_path or _resolve_input_path(edition)
    raw_text = source_path.read_text(encoding="utf-8")

    chunks = _split_by_chapter(raw_text)
    if not chunks:
        chunks = [raw_text.strip()]

    outputs: list[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        print(f"[REFINE-DE] KAISER {idx}/{len(chunks)}")
        refined = _run_agent(get_client(), model, kaiser_prompt, chunk)
        print(f"[REFINE-DE] BISMARCK {idx}/{len(chunks)}")
        polished = _run_agent(get_client(), model, bismarck_prompt, refined)
        outputs.append(polished.strip())

    merged = "\n\n".join(outputs).strip() + "\n"

    if write_output is None:
        if source_path.name.startswith("merge_") and output_path is None:
            write_output = True
        else:
            write_output = False

    if output_path is None:
        output_path, output_generic_path = _resolve_output_paths(edition, lang)
    else:
        output_generic_path = output_path

    if write_output:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(merged, encoding="utf-8")
        if output_generic_path != output_path:
            output_generic_path.write_text(merged, encoding="utf-8")

    return RefineDeResult(
        input_path=source_path,
        output_path=output_path,
        output_generic_path=output_generic_path,
        chunks=len(chunks),
        agent_kaiser=agent_kaiser,
        agent_bismarck=agent_bismarck,
        model=model,
        text=merged,
    )
