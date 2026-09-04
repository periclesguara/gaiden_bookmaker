from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..models import Chapter, ChapterSession, SourceDocument, StoryProject


BIBLE_FIELDS = (
    ("character_bible", "Bíblia do personagem"),
    ("antagonist_bible", "Bíblia do antagonista"),
    ("scenario_bible", "Cenários e locais"),
    ("world_bible", "Mundo, época, clima e referências"),
)
STORY_FIELDS = (
    ("story_direction", "Direção da história"),
    ("story_outline", "Roteiro geral"),
)


def _index_status(path_value: str, expected_sources: int, expected_model: str) -> dict:
    status = {
        "recorded": bool(path_value),
        "exists": False,
        "valid": False,
        "path": path_value,
        "source_count": 0,
        "chunk_count": 0,
        "dimension": 0,
        "embedding_model": "",
        "created_at": "",
        "reason": "Índice RAG ainda não criado.",
    }
    if not path_value:
        return status
    try:
        path = Path(path_value).expanduser().resolve(strict=True)
        if not path.is_file():
            status["reason"] = "O caminho registrado não é um arquivo de índice."
            return status
        status["exists"] = True
        with path.open(encoding="utf-8") as handle:
            header = json.loads(handle.readline())
        status.update(
            source_count=int(header.get("source_count", 0)),
            chunk_count=int(header.get("chunk_count", 0)),
            dimension=int(header.get("dimension", 0)),
            embedding_model=str(header.get("embedding_model", "")),
            created_at=str(header.get("created_at", "")),
        )
        problems = []
        if header.get("schema_version") != 1:
            problems.append("schema incompatível")
        if status["source_count"] != expected_sources:
            problems.append(
                f"índice contém {status['source_count']} de {expected_sources} fontes"
            )
        if status["chunk_count"] <= 0:
            problems.append("nenhum trecho vetorizado")
        if status["dimension"] <= 0:
            problems.append("dimensão de vetor inválida")
        if expected_model and status["embedding_model"] != expected_model:
            problems.append("modelo do índice difere do modelo configurado")
        if problems:
            status["reason"] = "; ".join(problems).capitalize() + "."
            return status
        status["valid"] = True
        status["reason"] = "Índice íntegro e compatível com a seleção atual."
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        status["reason"] = f"Não foi possível validar o índice: {exc}"
    return status


def _is_loopback(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname
    if not hostname:
        return False
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return hostname.casefold() == "localhost"


def _probe_local_model(base_url: str, model: str) -> tuple[bool | None, bool | None, str]:
    if not _is_loopback(base_url):
        return None, None, "Endpoint não local; verificação automática desativada."
    request = Request(f"{base_url}/models", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=0.75) as response:
            payload = json.load(response)
        installed = {
            str(item.get("id", ""))
            for item in payload.get("data", [])
            if isinstance(item, dict)
        }
        if model in installed:
            return True, True, "Serviço local respondeu e confirmou o modelo."
        return True, False, "Serviço local respondeu, mas não informou o modelo configurado."
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, False, f"Serviço local indisponível: {exc}"


def _model_status(*, probe: bool) -> dict:
    embedding_base_url = os.environ.get(
        "GAIDEN_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"
    ).rstrip("/")
    writing_base_url = os.environ.get(
        "GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"
    ).rstrip("/")
    embedding_model = os.environ.get(
        "GAIDEN_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
    )
    writing_model = os.environ.get("GAIDEN_QWEN_MODEL", "Qwen/Qwen3.5-9B")
    status = {
        "checked": False,
        "online": None,
        "embedding_online": None,
        "writing_online": None,
        "embedding_base_url": embedding_base_url,
        "writing_base_url": writing_base_url,
        "embedding_model": embedding_model,
        "writing_model": writing_model,
        "embedding_available": None,
        "writing_available": None,
        "reason": "Verificação do serviço não executada.",
    }
    if not probe:
        return status
    status["checked"] = True
    embedding_online, embedding_available, embedding_reason = _probe_local_model(
        embedding_base_url, embedding_model
    )
    writing_online, writing_available, writing_reason = _probe_local_model(
        writing_base_url, writing_model
    )
    status.update(
        embedding_online=embedding_online,
        writing_online=writing_online,
        embedding_available=embedding_available,
        writing_available=writing_available,
        online=embedding_online is True and writing_online is True,
        reason=f"Embeddings: {embedding_reason} Escrita: {writing_reason}",
    )
    return status


def _stage(number: int, label: str, complete: bool, detail: str) -> dict:
    return {
        "number": number,
        "label": label,
        "complete": complete,
        "detail": detail,
        "state": "complete" if complete else "blocked",
    }


def build_project_dashboard(project: StoryProject, *, probe_models: bool = True) -> dict:
    sources = list(project.sources.all())
    chapters = list(project.chapters.all())
    selected_count = len(sources)
    normalized_count = sum(
        source.status in {SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED}
        and bool(source.normalized_path and source.normalized_sha256)
        for source in sources
    )
    vectorized_count = sum(
        source.status == SourceDocument.Status.VECTORIZED and source.vectorized_at is not None
        for source in sources
    )
    failed_sources = [source for source in sources if source.status == SourceDocument.Status.FAILED]
    all_normalized = selected_count > 0 and normalized_count == selected_count

    models = _model_status(probe=probe_models)
    index = _index_status(
        project.vector_index_path,
        selected_count,
        models["embedding_model"],
    )
    all_vectorized = (
        selected_count > 0
        and vectorized_count == selected_count
        and index["valid"]
    )

    missing_bibles = [
        label for field, label in BIBLE_FIELDS if not getattr(project, field).strip()
    ]
    missing_story = [
        label for field, label in STORY_FIELDS if not getattr(project, field).strip()
    ]
    bibles_complete = not missing_bibles
    story_complete = not missing_story
    prepared_chapters = sum(bool(chapter.direction.strip() and chapter.script.strip()) for chapter in chapters)
    parameters_complete = bool(chapters) and prepared_chapters == len(chapters)
    generated_chapters = sum(
        chapter.status in {Chapter.Status.GENERATION_COMPLETE, Chapter.Status.FINAL}
        for chapter in chapters
    )
    final_chapters = sum(chapter.status == Chapter.Status.FINAL for chapter in chapters)
    generation_complete = bool(chapters) and generated_chapters == len(chapters)

    stages = [
        _stage(
            1,
            "Normalizar",
            all_normalized,
            f"{normalized_count} de {selected_count} fontes normalizadas",
        ),
        _stage(
            2,
            "Vetorizar",
            all_vectorized,
            (
                f"{index['chunk_count']} trechos · dimensão {index['dimension']}"
                if all_vectorized
                else index["reason"]
            ),
        ),
        _stage(
            3,
            "Bíblias",
            bibles_complete,
            "Concluídas" if bibles_complete else f"{len(missing_bibles)} campos pendentes",
        ),
        _stage(
            4,
            "Roteiro",
            story_complete,
            "Concluído" if story_complete else f"{len(missing_story)} campos pendentes",
        ),
        _stage(
            5,
            "Parâmetros",
            parameters_complete,
            f"{prepared_chapters} de {len(chapters)} capítulos preparados",
        ),
        _stage(
            6,
            "Gerar",
            generation_complete,
            f"{generated_chapters} de {len(chapters)} capítulos gerados",
        ),
    ]
    prior_complete = True
    current_stage = None
    for stage in stages:
        if stage["complete"]:
            stage["state"] = "complete"
        elif prior_complete and current_stage is None:
            stage["state"] = "current"
            current_stage = stage
        else:
            stage["state"] = "blocked"
        prior_complete = prior_complete and stage["complete"]

    global_generation_blockers = []
    if not all_vectorized:
        global_generation_blockers.append(index["reason"])
    global_generation_blockers.extend(f"Preencher {label.lower()}" for label in missing_bibles)
    global_generation_blockers.extend(f"Preencher {label.lower()}" for label in missing_story)
    if models["checked"] and not models["online"]:
        global_generation_blockers.append("Serviço local de modelos indisponível")
    elif models["checked"] and not models["writing_available"]:
        global_generation_blockers.append("Modelo de escrita não instalado")

    chapter_rows = []
    for chapter in chapters:
        blockers = list(global_generation_blockers)
        chapter_blockers = []
        if not chapter.direction.strip():
            chapter_blockers.append("Preencher direção do capítulo")
        if not chapter.script.strip():
            chapter_blockers.append("Preencher roteiro do capítulo")
        if chapter.status == Chapter.Status.FINAL:
            chapter_blockers.append("Capítulo já finalizado")
        elif chapter.status == Chapter.Status.GENERATING:
            chapter_blockers.append("Geração em andamento")
        elif chapter.status == Chapter.Status.GENERATION_COMPLETE:
            chapter_blockers.append("Geração concluída; revisar e finalizar")
        blockers.extend(chapter_blockers)
        complete_sessions = sum(
            session.status == ChapterSession.Status.COMPLETE for session in chapter.sessions.all()
        )
        chapter_rows.append(
            {
                "chapter": chapter,
                "direction_ready": bool(chapter.direction.strip()),
                "script_ready": bool(chapter.script.strip()),
                "generation_ready": not blockers,
                "generation_blockers": blockers,
                "chapter_blockers": chapter_blockers,
                "complete_sessions": complete_sessions,
            }
        )

    completed_stages = sum(stage["complete"] for stage in stages)
    vectorize_blockers = []
    if not selected_count:
        vectorize_blockers.append("Selecione ao menos uma fonte")
    if selected_count and not all_normalized:
        vectorize_blockers.append("Normalize todas as fontes selecionadas")
    if models["checked"] and not models["online"]:
        vectorize_blockers.append("Serviço local de modelos indisponível")
    elif models["checked"] and not models["embedding_available"]:
        vectorize_blockers.append("Modelo de embeddings não instalado")

    return {
        "sources": {
            "selected": selected_count,
            "normalized": normalized_count,
            "vectorized": vectorized_count,
            "failed": len(failed_sources),
            "all_normalized": all_normalized,
            "all_vectorized": all_vectorized,
        },
        "index": index,
        "models": models,
        "missing_bibles": missing_bibles,
        "missing_story": missing_story,
        "bibles_complete": bibles_complete,
        "story_complete": story_complete,
        "chapters": {
            "total": len(chapters),
            "prepared": prepared_chapters,
            "generated": generated_chapters,
            "final": final_chapters,
        },
        "chapter_rows": chapter_rows,
        "stages": stages,
        "completed_stages": completed_stages,
        "progress_percent": round(completed_stages / len(stages) * 100),
        "current_stage": current_stage,
        "vectorize_ready": not vectorize_blockers,
        "vectorize_blockers": vectorize_blockers,
        "global_generation_blockers": global_generation_blockers,
    }
