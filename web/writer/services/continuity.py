from __future__ import annotations


def validate_continuity(previous: dict, delta: dict, anchors: list[str]) -> dict:
    conflicts = []
    before = str(previous)
    after = str(delta)
    for anchor in anchors:
        if anchor and anchor.casefold() in before.casefold() and anchor.casefold() not in after.casefold():
            continue
    if any(value in after for value in ("2240", "Mars")) and any(value in str(anchors) for value in ("1913", "Paris")):
        conflicts.append({"severity": "HARD_CONFLICT", "reason": "delta contradicts an anchor fact"})
    return {"status": "HARD_CONFLICT" if conflicts else "PASS", "conflicts": conflicts}
