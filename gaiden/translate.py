from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.application.pipeline.translation."""

from gaiden.application.pipeline.translation import (
    assert_chunk_not_truncated,
    chunk_truncation_reason,
    run_translate_with_contract,
    sanitize_generated_chunk_text,
    text_has_complete_chunk_boundary,
)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m gaiden.translate <caminho_contrato.json>")
        raise SystemExit(1)
    run_translate_with_contract(sys.argv[1])
