from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.application.pipeline.ingest."""

from gaiden.application.pipeline.ingest import (
    ALLOWED_EXT,
    extract_text_from_file,
    extract_text_from_html,
    save_upload,
    sha256_bytes,
)
