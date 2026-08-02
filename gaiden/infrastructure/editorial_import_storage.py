from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable


STAGING_PREFIX = "gaiden-editorial-import-"


def stage_uploaded_package(package_file, manifest_file, artifact_files: Iterable) -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX)).resolve()
    package_path = root / "import-package.json"
    manifest_path = root / "manifest.json"
    _write_upload(package_path, package_file)
    _write_upload(manifest_path, manifest_file)
    seen = {package_path.name, manifest_path.name}
    for uploaded in artifact_files:
        name = Path(uploaded.name).name
        if not name or name in {".", ".."} or name in seen:
            raise ValueError(f"Nome de arquivo duplicado ou inseguro no upload: {name!r}")
        seen.add(name)
        _write_upload(root / name, uploaded)
    return {"root": root, "package": package_path, "manifest": manifest_path, "blocks": root}


def validate_staging_path(value: str | Path) -> Path:
    path = Path(value).resolve()
    staging_root = Path(tempfile.gettempdir()).resolve()
    path.relative_to(staging_root)
    if not any(part.startswith(STAGING_PREFIX) for part in path.parts):
        raise ValueError("Diretório temporário de importação inválido.")
    return path


def _write_upload(path: Path, uploaded) -> None:
    with path.open("xb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
