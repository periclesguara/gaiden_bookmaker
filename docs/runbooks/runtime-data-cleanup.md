# Runtime-data cleanup and recovery

## Scope

This cleanup removes 576 generated editorial artifacts under `data/` from the
Git index. It preserves only the reviewed source contracts in
`data/contracts/` and the `data/exports/README.md` placeholder. The existing
`.gitignore` rule for `data/**` and the repository hygiene check prevent a
future generated artifact from being committed.

## Operator storage and recovery

Before the index cleanup, the active local runtime root was migrated to:

```text
/home/periclesguara/Downloads/GAIDEN_DATA_ROOT
```

The 471 generated files still present in the checkout were compared byte-for-
byte with that external root before this change. No operator file was deleted.
The other 105 tracked paths were already absent from the checkout before the
cleanup and were not recreated.

To recover a previously tracked artifact that was already missing locally, use
the recovery commit `d9e10de8` in a disposable worktree or restore only the
required path:

```bash
git restore --source=d9e10de8 -- data/<required-path>
```

Do not restore generated files into Git. Place any recovered runtime artifact
under `GAIDEN_STORAGE_ROOT` instead.

## Rollback

Reverting the dedicated cleanup commit re-tracks the historical files, but is
not the preferred operational recovery. Keep `GAIDEN_STORAGE_ROOT` pointed at
the external root and restore individual artifacts only when required.
