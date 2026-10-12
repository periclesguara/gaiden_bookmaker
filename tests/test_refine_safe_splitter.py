from __future__ import annotations

from gaiden.application.pipeline.safe_text_splitter import (
    ends_with_pending_connector,
    split_text_by_paragraphs_sentence_aware,
)


def test_splitter_does_not_cut_after_pending_so_that_we_may():
    text = (
        "The city was first founded so that we might live, but continuing so that we may "
        "live well. This is the next sentence. This is another sentence."
    )

    chunks = split_text_by_paragraphs_sentence_aware(text, 75)

    assert chunks
    assert all(not chunk.rstrip().endswith("so that we may") for chunk in chunks)
    assert "so that we may live well." in "".join(chunks)


def test_pending_connector_detector_catches_real_boundary_tail():
    assert ends_with_pending_connector(
        "first founded so that we might live, but continuing so that we may"
    )
