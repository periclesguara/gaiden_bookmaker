from dataclasses import dataclass


@dataclass(frozen=True)
class SourceIngestionResult:
    work: object
    source: object
    canonical_text: object | None
