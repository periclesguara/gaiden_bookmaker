#!/usr/bin/env python3
"""Fail when tracked text files contain credential-shaped values.

Only file paths and rule identifiers are reported. Matched values are never
printed, logged, or included in CI artifacts.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

TOKEN_RULES = {
    "openai-key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(rb"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private-key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "credentialed-url": re.compile(
        rb"(?i)\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://"
        rb"[^\s/:]+:[^\s/@]+@"
    ),
}

SENSITIVE_NAMES = {
    "API_KEY",
    "DATABASE_PASSWORD",
    "DATABASE_URL",
    "DB_PASSWORD",
    "DJANGO_SECRET_KEY",
    "OPENAI_API_KEY",
    "PASSWORD",
    "PGPASSWORD",
    "SECRET_KEY",
    "TOKEN",
}

SAFE_MARKERS = {
    "",
    "change-me",
    "change-me-locally",
    "example",
    "placeholder",
    "replace-me",
    "replace-with-a-random-value",
}

ASSIGNMENT = re.compile(
    rb"(?im)^[ \t]*(?:export[ \t]+)?"
    rb"(?P<name>[A-Z][A-Z0-9_]*)[ \t]*=[ \t]*(?P<value>[^\r\n#]*)"
)


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        ROOT / raw.decode("utf-8")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def normalized_assignment(value: bytes) -> str:
    decoded = value.decode("utf-8", errors="ignore").strip()
    quote_characters = chr(34) + chr(39)
    if (
        len(decoded) >= 2
        and decoded[0] == decoded[-1]
        and decoded[0] in quote_characters
    ):
        decoded = decoded[1:-1].strip()
    return decoded


def is_safe_example(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered in SAFE_MARKERS
        or any(marker and marker in lowered for marker in SAFE_MARKERS)
        or "replace_me" in lowered
        or lowered.startswith("$")
        or lowered.startswith("_")
        or "os.environ" in lowered
        or "os.getenv" in lowered
        or lowered.startswith("_required_env(")
        or lowered.startswith("config(")
    )


def main() -> int:
    findings: set[tuple[str, str]] = set()

    for path in tracked_paths():
        if path == SELF or not path.is_file() or path.is_symlink():
            continue

        payload = path.read_bytes()
        if b"\0" in payload:
            continue

        relative = path.relative_to(ROOT).as_posix()

        for rule_name, pattern in TOKEN_RULES.items():
            if pattern.search(payload):
                findings.add((relative, rule_name))

        for match in ASSIGNMENT.finditer(payload):
            name = match.group("name").decode("ascii", errors="ignore")
            if name not in SENSITIVE_NAMES and not any(
                name.endswith(marker)
                for marker in ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN")
            ):
                continue
            value = normalized_assignment(match.group("value"))
            if value and not is_safe_example(value):
                findings.add((relative, "literal-sensitive-assignment"))

    if findings:
        for relative, rule_name in sorted(findings):
            print(
                f"possible secret: {relative} "
                f"({rule_name}; value redacted)",
                file=sys.stderr,
            )
        return 1

    print("tracked secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
