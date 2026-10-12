# Runtime artifacts review - 2026-06-12

Branch: `fix/book-0026-ptbr-refine-patch`

## Scope

This review audited tracked and untracked local/runtime artifacts before the next real smoke test for `modernize_en_us_2026`.

Areas checked:

- `web/data/**`
- `data/db/**`
- `data/tmp/**`
- `*.db`
- `*.sqlite3`
- local caches and generated outputs

## Tracked artifacts found

The following tracked runtime/generated artifacts were present in Git and already deleted from the working tree:

- `sqlite3`
- `web/data/README.md`
- `web/data/builds/book01_the_adventures_of_sherlock_holmes/de/BOOK.BUILD.MD`
- `web/data/builds/book01_the_adventures_of_sherlock_holmes/de/kdp_merged.md`
- `web/data/db/gaiden.sqlite3`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/book01_the_adventures_of_sherlock_holmes/de/about_edition.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/book01_the_adventures_of_sherlock_holmes/de/copyright.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/book01_the_adventures_of_sherlock_holmes/de/frontispiece.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/de/about_edition.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/de/copyright.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/de/frontispiece.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/en/copyright.md`
- `web/data/frontmatter/book01_the_adventures_of_sherlock_holmes/en/frontispiece.md`
- `web/data/translated/book01_the_adventures_of_sherlock_holmes/de/miolo.md`

## Removed from Git index

Removed with `git rm --cached`:

- `sqlite3`
- `web/data/**`

These paths are runtime/generated data and should not be tracked by default.

## Kept versioned

- `docs/audit/agent_runs/.gitkeep`

The directory is kept so internal agent execution audits have a stable local target, while generated audit files stay ignored.

## Removed locally

- `.pytest_cache/`

No tracked source file was removed from the working tree as part of this cleanup. The `web/data/**` files and `sqlite3` marker were already absent before the index cleanup.

## Needs human decision

- Existing broader worktree changes outside runtime data remain untouched.
- Generated package metadata under `gaiden_bookmaker.egg-info/` remains modified/untracked and should be handled separately.
- Local backup files under `backups/repo_cleanup/` were preserved.

## .gitignore changes

Runtime/local protections now include:

- `*.db`
- `*.sqlite3`
- `data/db/`
- `data/tmp/`
- `web/data/`
- `docs/audit/*`
- `!docs/audit/agent_runs/`
- `docs/audit/agent_runs/*`
- `!docs/audit/agent_runs/.gitkeep`

## Recommendation

Keep this cleanup separate from feature work. The next branch should start from a state where runtime data is ignored and only code/config/docs needed for the smoke test are staged.
