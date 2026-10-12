from __future__ import annotations

import json


def build_model_neutral_prompt(contract: dict) -> dict:
    return {
        "system": "Write one complete chapter. Obey operator instructions, anchors, continuity and the chapter plan. Return only prose.",
        "user": json.dumps(contract, ensure_ascii=False, sort_keys=True),
    }
