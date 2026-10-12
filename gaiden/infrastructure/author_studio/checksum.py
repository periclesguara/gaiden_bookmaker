from __future__ import annotations

import hashlib


def calculate_sha256(file_obj) -> str:
    digest = hashlib.sha256()
    position = file_obj.tell() if hasattr(file_obj, "tell") else None
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
        digest.update(chunk)
    if position is not None and hasattr(file_obj, "seek"):
        file_obj.seek(position)
    return digest.hexdigest()
