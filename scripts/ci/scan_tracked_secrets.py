#!/usr/bin/env python3
"""Fail when tracked text files contain credential-shaped values.

Only paths and rule identifiers are reported. Matched values are never printed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
RULES = {
    "openai-key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(rb"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def main() -> int:
    findings: list[tuple[str, str]] = []
    for path in tracked_paths():
        if path == SELF or not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for rule_name, pattern in RULES.items():
            if pattern.search(payload):
                findings.append((relative, rule_name))

    if findings:
        for relative, rule_name in findings:
            print(f"possible secret: {relative} ({rule_name}; value redacted)", file=sys.stderr)
        return 1
    print("tracked secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
