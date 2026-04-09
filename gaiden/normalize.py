from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.application.pipeline.normalization."""

from gaiden.application.pipeline.normalization import (
    normalize_text_v1,
    normalize_text_v2,
    roman_to_int,
    sha256_text,
    write_normalized,
)
