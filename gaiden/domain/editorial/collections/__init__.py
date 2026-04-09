from __future__ import annotations

COLLECTION_STATUS_CREATED = "COLLECTION_CREATED"
COLLECTION_STATUS_ITEMS_REGISTERED = "COLLECTION_ITEMS_REGISTERED"
COLLECTION_STATUS_UPLOADS_RECEIVED = "COLLECTION_UPLOADS_RECEIVED"
COLLECTION_STATUS_PREPARED = "COLLECTION_PREPARED"
COLLECTION_STATUS_NORMALIZED = "COLLECTION_NORMALIZED"
COLLECTION_STATUS_MERGED = "COLLECTION_MERGED"
COLLECTION_STATUS_READY_FOR_PIPELINE = "COLLECTION_READY_FOR_PIPELINE"
COLLECTION_STATUS_PIPELINE_RUNNING = "COLLECTION_PIPELINE_RUNNING"
COLLECTION_STATUS_DONE = "COLLECTION_DONE"
COLLECTION_STATUS_FAILED = "COLLECTION_FAILED"

COLLECTION_STATUS_CHOICES = [
    (COLLECTION_STATUS_CREATED, "Created"),
    (COLLECTION_STATUS_ITEMS_REGISTERED, "Items registered"),
    (COLLECTION_STATUS_UPLOADS_RECEIVED, "Uploads received"),
    (COLLECTION_STATUS_PREPARED, "Prepared"),
    (COLLECTION_STATUS_NORMALIZED, "Normalized"),
    (COLLECTION_STATUS_MERGED, "Merged"),
    (COLLECTION_STATUS_READY_FOR_PIPELINE, "Ready for pipeline"),
    (COLLECTION_STATUS_PIPELINE_RUNNING, "Pipeline running"),
    (COLLECTION_STATUS_DONE, "Done"),
    (COLLECTION_STATUS_FAILED, "Failed"),
]

ITEM_STATUS_PENDING = "pending"
ITEM_STATUS_RUNNING = "running"
ITEM_STATUS_FAILED = "failed"
ITEM_STATUS_COMPLETED = "completed"

ITEM_STATUS_CHOICES = [
    (ITEM_STATUS_PENDING, "Pending"),
    (ITEM_STATUS_RUNNING, "Running"),
    (ITEM_STATUS_FAILED, "Failed"),
    (ITEM_STATUS_COMPLETED, "Completed"),
]

COLLECTION_KIND_CHOICES = [
    ("novel_trilogy", "Novel trilogy"),
    ("complete_novels", "Complete novels"),
    ("collected_tales", "Collected tales"),
    ("selected_stories", "Selected stories"),
    ("omnibus", "Omnibus"),
    ("anthology", "Anthology"),
    ("mixed_collection", "Mixed collection"),
]


def validate_item_count(value: int) -> None:
    if value < 2 or value > 10:
        raise ValueError("Collection must have between 2 and 10 items.")


def validate_contiguous_order(order_indexes: list[int]) -> None:
    if not order_indexes:
        raise ValueError("Collection must contain at least 2 items.")
    ordered = sorted(order_indexes)
    expected = list(range(1, len(ordered) + 1))
    if ordered != expected:
        raise ValueError(f"Collection items must have contiguous order indexes: expected {expected}, got {ordered}.")


def validate_no_duplicates(keys: list[tuple[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"Duplicate collection item detected: {key}.")
        seen.add(key)


def can_handoff_to_pipeline(status: str) -> bool:
    return status in {
        COLLECTION_STATUS_MERGED,
        COLLECTION_STATUS_READY_FOR_PIPELINE,
        COLLECTION_STATUS_PIPELINE_RUNNING,
        COLLECTION_STATUS_DONE,
    }
