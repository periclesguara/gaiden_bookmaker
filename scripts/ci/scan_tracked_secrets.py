#!/usr/bin/env python3
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


def main() -> int:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, stdout=subprocess.PIPE)
    findings = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = ROOT / raw.decode("utf-8")
        if path == SELF or not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        for name, pattern in RULES.items():
            if pattern.search(payload):
                findings.append((path.relative_to(ROOT).as_posix(), name))
    for path, rule in findings:
        print(f"possible secret: {path} ({rule}; value redacted)", file=sys.stderr)
    if findings:
        return 1
    print("tracked secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
