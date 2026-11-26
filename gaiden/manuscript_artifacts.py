from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise FileExistsError(
                    f"Preserved existing artifact: {path.name}. "
                    "A different revision requires explicit editorial reconciliation."
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def preserve_manuscript(path: Path, payload: bytes) -> dict[str, str]:
    text = payload.decode("utf-8")
    if not text.strip():
        raise ValueError("An empty manuscript cannot be promoted.")
    digest = hashlib.sha256(payload).hexdigest()
    snapshot = path.parent / ".manuscript_versions" / f"{digest}.md"
    write_once(snapshot, payload)
    write_once(path, payload)
    return {"path": str(path), "sha256": digest, "snapshot": str(snapshot)}
