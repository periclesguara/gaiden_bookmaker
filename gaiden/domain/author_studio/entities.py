from dataclasses import dataclass


@dataclass(frozen=True)
class CoreTextResult:
    text: str
    needs_review: bool
    confidence: float
    removed_sections: tuple[str, ...] = ()
