import os
import subprocess
from pathlib import Path

from . import utils

CORE_DOCKER_STEPS = {"translate", "refine", "polish"}
DEFAULT_CORE_DOCKER_LANGS = ("en", "ptbr", "es", "de", "it", "fr")


def _normalize_lang(value: str | None) -> str:
    return utils.normalize_lang(value)


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def docker_langs_from_env() -> tuple[str, ...]:
    raw = (os.environ.get("GAIDEN_CORE_DOCKER_LANGS") or "").strip()
    if not raw:
        return DEFAULT_CORE_DOCKER_LANGS
    langs = tuple(filter(None, (_normalize_lang(item) for item in raw.split(","))))
    return langs or DEFAULT_CORE_DOCKER_LANGS


def docker_isolation_enabled() -> bool:
    return _as_bool(os.environ.get("GAIDEN_CORE_DOCKER_ENABLED") or "1")


def should_run_in_docker(step: str, language: str) -> bool:
    return (
        docker_isolation_enabled()
        and step in CORE_DOCKER_STEPS
        and _normalize_lang(language) in set(docker_langs_from_env())
    )


def service_name_for_language(language: str) -> str:
    return f"gaiden-core-{_normalize_lang(language)}"


def compose_file_path(project_root: Path) -> Path:
    return project_root / "docker-compose.core.yml"


def build_docker_command(
    *,
    project_root: Path,
    edition_id: int,
    step: str,
    language: str,
    target_language: str | None = None,
    refine_profile: str | None = None,
) -> list[str]:
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_file_path(project_root)),
        "run",
        "--rm",
        service_name_for_language(language),
        "python",
        "web/manage.py",
        "run_core_lang_step",
        "--edition-id",
        str(edition_id),
        "--step",
        step,
    ]
    if target_language:
        cmd.extend(["--target-language", target_language])
    if refine_profile:
        cmd.extend(["--refine-profile", refine_profile])
    return cmd


def run_docker_core_step(
    *,
    project_root: Path,
    edition_id: int,
    step: str,
    language: str,
    target_language: str | None = None,
    refine_profile: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = build_docker_command(
        project_root=project_root,
        edition_id=edition_id,
        step=step,
        language=language,
        target_language=target_language,
        refine_profile=refine_profile,
    )
    return subprocess.run(
        cmd,
        cwd=project_root,
        text=True,
        capture_output=True,
        check=True,
    )
