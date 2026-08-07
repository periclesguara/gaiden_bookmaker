# Runtime artifact cleanup

Date: 2026-08-07

## Scope

This cleanup removes 552 generated files from the current Git tree under:

- `data/chunks/`;
- `data/builds/`;
- `data/translated/`.

It removes 191,602 generated text lines. It does not delete source code,
migrations, tests, application configuration, or configured external storage.

The removed files remain recoverable from commit
`636613a34936bccddf687566d5f5e119d6021a69` and earlier repository history.
No history rewrite or force-push is part of this change.

## Operator precaution

A checkout or pull that applies this deletion can remove the previously tracked
copies from that worktree. Before updating an operator machine, copy any
canonical or unfinished work from the affected directories to the configured
external editorial storage and verify that backup.

Because these paths are ignored, files created there after the cleanup remain
local and are not re-added by ordinary Git operations.

## Prevention

The repository hygiene check now rejects tracked content under:

- `data/chunks/`;
- `data/builds/`;
- `data/covers/`;
- `data/images/`;
- `data/translated/`.

Small test fixtures must live in an explicit fixture directory and be reviewed
as source-controlled test inputs.

## Repository size

Removing files from the current tree prevents new clones from checking out these
artifacts, but the historical objects remain in Git. Reducing the stored
repository history requires an optional, separately approved history cleanup.
Rewriting shared history is destructive and remains governed by the credential
and history-cleanup runbook.
