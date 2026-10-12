#!/usr/bin/env python3
"""Build a generic incremental manifest from an explicit block index.

The index supplies editorial identities and file paths; this script only
computes immutable file metadata and the resume prefix. It never infers a
block identity from a title, author or temporary file name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


CONFIRMED_STATUSES = {"READY", "IMPORTED", "IN_PROGRESS", "RETURNED", "APPROVED"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_manifest_sha256(payload: dict) -> str:
    canonical = dict(payload)
    canonical.pop("manifest_sha256", None)
    data = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(data)


def build_manifest(index_path: Path) -> dict:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    root = index_path.parent
    required = {
        "job_id",
        "work_id",
        "edition_id",
        "book_code",
        "locale",
        "status",
        "expected_block_count",
        "blocks",
    }
    missing = sorted(required - set(index))
    if missing:
        raise ValueError("Campos ausentes no índice: " + ", ".join(missing))

    blocks = []
    seen_sequences: set[int] = set()
    seen_ids: set[str] = set()
    for item in sorted(index["blocks"], key=lambda row: row["sequence"]):
        path = Path(item["file_path"]).expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        data = path.read_bytes()
        if not data:
            raise ValueError(f"Bloco vazio: {path}")
        sequence = int(item["sequence"])
        block_id = str(item["block_id"])
        if sequence in seen_sequences or block_id in seen_ids:
            raise ValueError(f"Identidade duplicada: sequence={sequence}, block_id={block_id}")
        seen_sequences.add(sequence)
        seen_ids.add(block_id)
        blocks.append(
            {
                "sequence": sequence,
                "block_id": block_id,
                "file_name": item.get("file_name") or path.name,
                "content_sha256": sha256_bytes(data),
                "size_bytes": len(data),
                "status": item.get("status", "READY"),
                "version": int(item.get("version", 1)),
                "source_block_id": item.get("source_block_id"),
                "updated_at": item.get("updated_at")
                or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    confirmed = {block["sequence"] for block in blocks if block["status"] in CONFIRMED_STATUSES}
    contiguous = 0
    while contiguous + 1 in confirmed:
        contiguous += 1
    expected = int(index["expected_block_count"])
    manifest = {
        "schema_version": 1,
        "job_id": index["job_id"],
        "work_id": index["work_id"],
        "edition_id": index["edition_id"],
        "book_code": index["book_code"],
        "locale": index["locale"],
        "status": index["status"],
        "expected_block_count": expected,
        "last_contiguous_sequence": contiguous,
        "next_sequence": None if contiguous >= expected else contiguous + 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "blocks": blocks,
    }
    manifest["manifest_sha256"] = canonical_manifest_sha256(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path, help="JSON com identidades editoriais e file_path de cada bloco")
    parser.add_argument("output", type=Path, help="Destino do manifest.json")
    args = parser.parse_args()
    manifest = build_manifest(args.index.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    print(f"blocks={len(manifest['blocks'])}")
    print(f"next_sequence={manifest['next_sequence']}")
    print(f"manifest_sha256={manifest['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
